#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
import base64
import hashlib
import locale
import mimetypes
import os
import re
import ssl
import logging
import struct
import sys
import urllib
import uuid
import weakref
from abc import abstractmethod
from cgi import parse_header, MiniFieldStorage, valid_boundary
from email.feedparser import FeedParser
from email.message import Message
from io import BytesIO, TextIOWrapper
from typing import Mapping

from requests_toolbelt import MultipartDecoder

import trio
import trio_asyncio
from remi import gui

from trio_websocket import serve_websocket, ConnectionClosed

from .server import (
    to_websocket, from_websocket,
    encode_text,
    get_method_by_id,
    get_method_by_name,
    parse_session_cookie,
    parse_parametrs,
    App as SyncApp,
    unquote, unquote_to_bytes,
    parse_qs, urlparse,
    runtimeInstances
)


class SingletonDecorator:

    def __init__(self,klass):
        self.klass = klass
        self.instance = None

    def __call__(self, *args, **kwds):
        if self.instance is None:
            self.instance = self.klass(*args, **kwds)
        return self.instance


@SingletonDecorator
class Globals(object):

    def __init__(self):
        self.items = dict()

    def set(self, name, value):
        self.items[name] = value

    def get(self, name):
        return self.items.get(name, None)


@SingletonDecorator
class ClientsManager(object):

    def __init__(self):
        self.clients = {}

    def get(self, cookie):
        # print("GETTING CLINT BY COOKIE", cookie)
        return self.clients.get(cookie, None)

    def add_client(self, cookie, application: 'Application'):
        # print("SETTING CLIENT BY COOKIE", cookie)
        self.clients[cookie] = application

    def remove_client(self, cookie):
        pass


_MSG_ACK = '3'
_MSG_JS = '2'
_MSG_UPDATE = '1'


class ATag(gui.Tag):

    async def innerHTML(self, local_changed_widgets):
        ret = ''
        for k in self._render_children_list:
            s = self.children[k]
            if isinstance(s, ATag):
                ret = ret + await s.repr(local_changed_widgets)
            elif isinstance(s, type('')):
                ret = ret + s
            elif isinstance(s, type(u'')):
                ret = ret + s.encode('utf-8')
            else:
                ret = ret + repr(s)
        return ret

    async def repr(self, changed_widgets=None):
        """It is used to automatically represent the object to HTML format
        packs all the attributes, children and so on.

        Args:
            changed_widgets (dict): A dictionary containing a collection of tags that have to be updated.
                The tag that have to be updated is the key, and the value is its textual repr.
        """
        if changed_widgets is None:
            changed_widgets = {}
        local_changed_widgets = {}
        _innerHTML = await self.innerHTML(local_changed_widgets)

        if self._ischanged() or ( len(local_changed_widgets) > 0 ):
            self._backup_repr = ''.join(('<', self.type, ' ', self._repr_attributes, '>',
                                         _innerHTML, '</', self.type, '>'))
            #faster but unsupported before python3.6
            #self._backup_repr = f'<{self.type} {self._repr_attributes}>{_innerHTML}</{self.type}>'
        if self._ischanged():
            # if self changed, no matter about the children because will be updated the entire parent
            # and so local_changed_widgets is not merged
            changed_widgets[self] = self._backup_repr
            self._set_updated()
        else:
            changed_widgets.update(local_changed_widgets)
        return self._backup_repr

    async def _need_update(self, emitter=None):
        #if there is an emitter, it means self is the actual changed widget
        if emitter:
            tmp = dict(self.attributes)
            if len(self.style):
                tmp['style'] = gui.jsonize(self.style)
            self._repr_attributes = ' '.join('%s="%s"' % (k, v) if v is not None else k for k, v in
                                             tmp.items())
        if not self.ignore_update:
            if self.get_parent():
                await self.get_parent()._need_update()


class Headers(object):

    def __init__(self, headers: dict):
        self._headers = headers

    @property
    def cookie(self):
        return self._headers.get('cookie', None)


class RequestProcessor(object):

    def __init__(self, stream: trio.SocketStream):
        self.stream = stream

    async def handle(self):
        pass


class WebSocketHandler(object):
    magic = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

    def __init__(self, cookie: str, headers, stream: trio.SocketStream):
        self._headers = headers
        self.session = None
        self.cookie = cookie
        self.stream: trio.SocketStream = stream
        if isinstance(stream, trio.SSLStream):
            self.client_address = stream.transport_stream.socket.getpeername()[0]
        else:
            self.client_address = stream.socket.getpeername()[0]
        self.handshake_done = False
        self._log = logging.getLogger('remi.aserver.ws')
        self.clients_manager = ClientsManager()

        self.application: 'Application' = self.clients_manager.get(self.cookie)
        self.application.websockets.append(self)
        self.closed = False

    async def handle(self, nursery):
        self._log.debug("ws: handle called!")
        if await self.handshake():
            while not self.closed:
                if not await self.read_next_message():
                    self.clients_manager.remove_client(
                        self)
                    self._log.debug(
                        'ws ending websocket service ...')
                    break

    async def handshake(self):
        self._log.debug("handhake")
        key: str = self._headers['Sec-WebSocket-Key']

        cookie = self.cookie
        # cookie = self._headers.get('cookie')
        # if cookie:
        #     self.session = parse_session_cookie(cookie)
        # if self.session is None:
        #     return False
        # if self.session not in self.clients_manager:
        #     return False
        digest = hashlib.sha1(
            key.encode('utf-8') + self.magic)
        digest = digest.digest()
        digest = base64.b64encode(digest)
        digest = digest.decode()
        response = 'HTTP/1.1 101 Switching Protocols\r\n'
        response += 'Upgrade: websocket\r\n'
        response += 'Connection: Upgrade\r\n'
        response = response + \
                   f"Sec-WebSocket-Accept: {digest}\r\n\r\n"

        self._log.debug(f"sending response {response}")

        await self.stream.send_all(response.encode('utf-8'))
        self._log.info('handshake complete')
        self.handshake_done = True

        await self.application.ws_handshake_done(self)
        return True

    async def read(self, amount):
        return await self.stream.receive_some(amount)

    async def read_next_message(self):
        # noinspection PyBroadException
        try:
            try:
                length = await self.read(2)
            except ValueError:
                # socket was closed, just return without errors
                return False
            length = length[1] & 127
            if length == 126:
                length = struct.unpack('>H', await self.read(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', await self.read(8))[0]
            masks = [byte for byte in await self.read(4)]
            decoded = ''
            for char in await self.read(length):
                decoded += chr(char ^ masks[len(decoded) % 4])
            await self.on_message(from_websocket(decoded))
        except Exception:
            return False
        return True
        pass

    async def send_message(self, message):

        if not self.handshake_done:
            self._log.warning("ignoring message %s (handshake not done)" % message[:10])
            return
        if isinstance(message, str):
            message = message.encode()

        # self._log.debug('send_message: %s... -> %s' % (message[:10], self.client_address))
        out = bytearray()
        out.append(129)
        length = len(message)
        if length <= 125:
            out.append(length)
        elif 126 <= length <= 65535:
            out.append(126)
            out += struct.pack('>H', length)
        else:
            out.append(127)
            out += struct.pack('>Q', length)
        out = out + message
        await self.stream.send_all(out)

    async def on_message(self, message):
        # TODO: adapt
        global runtimeInstances

        await self.send_message(_MSG_ACK)
        async with self.application.update_lock:
            # noinspection PyBroadException
            try:
                # saving the websocket in order to update the client
                if self not in self.application.websockets:
                    self.application.websockets.append(self)

                # parsing messages
                chunks = message.split('/')
                # self._log.debug('on_message: %s' % chunks[0])

                if len(chunks) > 3:  # msgtype,widget,function,params
                    # if this is a callback
                    msg_type = 'callback'
                    if chunks[0] == msg_type:
                        widget_id = chunks[1]
                        function_name = chunks[2]
                        params = message[
                                 len(msg_type) + len(widget_id) + len(function_name) + 3:]

                        param_dict = parse_parametrs(params)
                        # print("widget", widget_id)
                        # print(function_name, param_dict)

                        try:
                            callback = get_method_by_name(runtimeInstances[widget_id], function_name)
                            # print(runtimeInstances[widget_id], callback)
                            if callback is not None:
                                callback(**param_dict)
                        except Exception as e:
                            pass

            except Exception:
                self._log.error('error parsing websocket', exc_info=True)

    async def close(self):
        if isinstance(self.stream, trio.SSLStream):
            await self.stream.aclose()
        else:
            await self.stream.send_eof()
        await self.stream.wait_send_all_might_not_block()
        self.closed = True


class Application(object):

    def __init__(self, cookie: str, stream: trio.SocketStream, headers: dict, server: 'AServer'):
        self.logger = logging.getLogger('remi.aserver.Application')
        self._log = self.logger
        self.stream = stream
        self.headers = headers
        self.clients_manger = ClientsManager()
        self.foreground_workers = list()
        self.cookie = cookie
        self.started = False
        self.active = False
        self.nursery = None
        self.server = server

        self.websockets = list()

        self.update_lock = trio.Lock()

        self._need_update_flag = False
        self._stop_update_flag = False
        self._root_changed = False
        self.update_interval = 0.1

        self.root = None
        self.page = None

        self.build_base_page()

    async def _idle_loop(self):
        async with trio.open_nursery() as nursery:
            # self.logger.debug(f"ENTERED _idle_loop ")
            while not self._stop_update_flag:
                # self.logger.debug(f"self._stop_update_flag = {self._stop_update_flag}")

                while len(self.foreground_workers) > 0:

                    worker = self.foreground_workers.pop(0)
                    # self.logger.debug("ADDING NEW WORKER!!!")
                    # self.logger.debug(str(worker))
                    nursery.start_soon(worker, nursery)

                # self.logger.debug(f"sleeping for {self.update_interval}")
                await trio.sleep(
                    self.update_interval)
                # self.logger.debug("wake upping ...")
                # async with self.update_lock:
                #     if self._need_update_flag:
                #         await self.do_gui_update()
                if self._need_update_flag:
                    # print("NEED UPDATE!!!!")
                    await self.do_gui_update()
                elif self._root_changed:
                    # self.logger.debug("ROOT CHANGED!!!")
                    self._root_changed = False
                    await self._send_spontaneous_ws_msg(
                        "0" +
                        self.root.identifier + ',' +
                        to_websocket(self.page.children['body'].innerHTML({}))
                    )

    def onload(self, emitter):
        """ WebPage Event that occurs on webpage loaded
        """
        self._log.debug('App.onload event occurred')

    def onerror(self, emitter, message, source, lineno, colno):
        """ WebPage Event that occurs on webpage errors
        """
        self._log.debug("""App.onerror event occurred in webpage: 
            \nMESSAGE:%s\nSOURCE:%s\nLINENO:%s\nCOLNO:%s\n"""%(message, source, lineno, colno))

    def ononline(self, emitter):
        """ WebPage Event that occurs on webpage goes online after a disconnection
        """
        self._log.debug('App.ononline event occurred')

    def onpagehide(self, emitter):
        """ WebPage Event that occurs on webpage when the user navigates away
        """
        self._log.debug('App.onpagehide event occurred')

    def onpageshow(self, emitter):
        """ WebPage Event that occurs on webpage gets shown
        """
        self._log.debug('App.onpageshow event occurred')

    def onresize(self, emitter, width, height):
        """ WebPage Event that occurs on webpage gets resized
        """
        self._log.debug('App.onresize event occurred. Width:%s Height:%s'%(width, height))

    def on_close(self):
        """ Called by the server when the App have to be terminated
        """
        self._stop_update_flag = True
        for ws in self.websockets:
            ws.close()

    def close(self):
        """TODO: implement!"""

    def build_base_page(self):

        head = gui.HEAD(self.__class__.__name__)
        # use the default css, but append a version based on its hash, to stop browser caching
        head.add_child('internal_css', "<link href='/res:style.css' rel='stylesheet' />\n")
        body = gui.BODY()
        body.onload.connect(self.onload)
        body.onerror.connect(self.onerror)
        body.ononline.connect(self.ononline)
        body.onpagehide.connect(self.onpagehide)
        body.onpageshow.connect(self.onpageshow)
        body.onresize.connect(self.onresize)
        self.page = gui.HTML()
        self.page.add_child('head', head)
        self.page.add_child('body', body)

    def set_page_internals(self, stream: trio.SocketStream, headers: dict):

        if isinstance(stream, trio.SocketStream):
            sockname = stream.socket.getsockname()
            peername = stream.socket.getpeername()
        elif isinstance(stream, trio.SSLStream):
            sockname = stream.transport_stream.socket.getsockname()
            peername = stream.transport_stream.socket.getpeername()
        else:
            raise TypeError(type(stream))

        net_interface_ip = headers.get(
            'Host', "{}:{}".format(
                sockname[0],
                peername[1])
        )
        websocket_timeout_timer_ms = str(1000)
        pending_messages_queue_length = str(1000)
        self.page.children['head'].set_internal_js(
            net_interface_ip,
            pending_messages_queue_length,
            websocket_timeout_timer_ms)

    @classmethod
    async def create(cls, cookie: str, stream: trio.SocketStream, headers: dict, server=None):
        logging.debug("CREATING Application")
        # print(cls, cookie, stream, headers, server)
        application = cls(cookie, stream, headers, server)
        return application

    async def handle_request(self, stream, method, path, query, data, headers):
        self.logger.debug("handle_request called")
        self.logger.debug(''.join(map(str, (method, path, query, len(data) if data else None))))

        if method == "GET":
            return await self.handle_get(
                stream, path, query, data, headers)
        elif method == "POST":
            return await self.handle_post(
                stream, path, query, data, headers)
        elif method == "HEAD":
            return await self.handle_head(stream, path, query, data, headers)

    async def send_response(self, stream: trio.SocketStream, code: int):
        await stream.send_all(f"HTTP/1.1 {code} OK\r\n".encode())

    async def send_header(self, stream: trio.SocketStream, name, value):
        await stream.send_all(f"{name}: {value}\r\n".encode())

    async def end_headers(self, stream):
        await stream.send_all("\r\n".encode())

    async def send(self, stream, data):
        if data is None:
            return
        if isinstance(data, str):
            data = data.encode()
        await stream.send_all(data)

    def set_root_widget(self, widget):
        self.page.children['body'].append(widget, 'root')
        self.root = widget
        self.root.disable_refresh()
        self.root.attributes['data-parent-widget'] = str(id(self))
        self.root._parent = self
        self.root.enable_refresh()
        self._root_changed = True

    async def ws_handshake_done(self, ws_instance_to_update):
        async with self.update_lock:
            if self.root is None:
                self.set_root_widget(self.main())
            msg = "0" + self.root.identifier + ',' + to_websocket(self.page.children['body'].innerHTML({}))
        await ws_instance_to_update.send_message(msg)

    def get_file_content(self, filename):
        self.logger.debug(f"getting content of {filename}")
        try:
            f = open(filename, "rb")
            return f.read()
        except Exception as e:
            return None

    def _get_static_file(self, filename):

        filename = filename.replace("..", "") #avoid backdirs
        __i = filename.find(':')
        if __i < 0:
            return None
        key = filename[:__i]
        path = filename[__i+1:]
        key = key.replace("/","")
        paths = {'res': os.path.join(os.path.dirname(__file__), "res")}
        try:
            static_paths = self._app_args.get(
                'static_file_path', {})
        except AttributeError:
            static_paths = {}
        if not type(static_paths)==dict:
            self._log.error("App's parameter static_file_path must be a Dictionary.", exc_info=False)
            static_paths = {}
        paths.update(static_paths)
        if not key in paths:
            return None
        return os.path.join(paths[key], path)

    async def process_all(self, stream, headers, func):
        self.logger.debug('get: %s' % func)

        static_file = SyncApp.re_static_file.match(func)
        attr_call = SyncApp.re_attr_call.match(func)

        if (func == '/') or (not func):
            await self.send_response(stream, 200)
            await self.send_header(
                stream,
                f"Set-Cookie", f"cookie={self.cookie}")
            await self.send_header(
                stream, 'Content-type', 'text/html')
            await self.end_headers(stream)

            async with self.update_lock:
                # render the HTML
                self.set_page_internals(stream, headers)
                try:
                    page_content = self.page.repr()
                except Exception as e:
                    print("Exception occured !!!")
                    await self.notification_message(title="error ocurred", content=str(e))
                    return

            await self.send(
                stream,
                encode_text("<!DOCTYPE html>\n"))
            await self.send(
                stream,
                encode_text(page_content))
            self.logger.debug("CONTENT WAS SENT!!!")

        elif static_file:
            filename = self._get_static_file(static_file.groups()[0])
            if not filename:
                await self.send_response(stream, 404)
                return
            mimetype, encoding = mimetypes.guess_type(filename)
            await self.send_response(stream, 200)
            await self.send_header(stream, 'Content-type', mimetype if mimetype else 'application/octet-stream')
            # if self.server.enable_file_cache:
            #     self.send_header('Cache-Control', 'public, max-age=86400')
            await self.end_headers(stream)

            content = await trio.run_sync_in_worker_thread(self.get_file_content, filename)

            await self.send(stream, content)

        elif attr_call:
            # with self.update_lock:
            param_dict = parse_qs(urlparse(func).query)
            # parse_qs returns patameters as list, here we take the first element
            for k in param_dict:
                param_dict[k] = param_dict[k][0]

            widget, func = attr_call.group(1, 2)
            try:
                content, headers = get_method_by_name(get_method_by_id(widget), func)(**param_dict)
                if content is None:
                    await self.send_response(stream, 503)
                    return
                await self.send_response(stream, 200)
            except IOError:
                self._log.error('attr %s/%s call error' % (widget, func), exc_info=True)
                await self.send_response(stream, 404)
                return
            except (TypeError, AttributeError):
                self._log.error('attr %s/%s not available' % (widget, func))
                await self.send_response(stream, 503)
                return

            for k in headers:
                await self.send_header(stream, k, headers[k])
            await self.end_headers(stream)
            try:
                await self.send(stream, content)
            except TypeError:
                await self.send(
                    stream, encode_text(content))

    def _need_update(self, emitter=None):
        self._need_update_flag = True
        return
        if self.update_interval == 0:
            #no interval, immadiate update
            # await self.do_gui_update()
            pass
        else:
            #will be updated after idle loop
            self._need_update_flag = True

    def set_need_update(self):
        self._need_update_flag = True

    async def do_gui_update(self):
        """ This method gets called also by Timer, a new thread, and so needs to lock the update
        """
        async with self.update_lock:
            changed_widget_dict = {}
            try:
                self.root.repr(changed_widget_dict)
            except KeyError:
                pass
            for widget in changed_widget_dict.keys():
                # print("CHANGED WIDGET!", widget)
                html = changed_widget_dict[widget]
                __id = str(widget.identifier)

                await self._send_spontaneous_ws_msg(
                    _MSG_UPDATE + __id + ',' + to_websocket(html))
        self._need_update_flag = False

    async def _send_spontaneous_ws_msg(self, message):
        for ws in self.websockets:
            ws: WebSocketHandler
            try:
                await ws.send_message(message)
            except Exception as e:
                try:
                    self.websockets.remove(ws)
                    await ws.close()
                except:
                    pass

    async def execute_javascript(self, code):
        await self._send_spontaneous_ws_msg(_MSG_JS + code)

    async def notification_message(self, title, content, icon=""):

        """This function sends "javascript" message to the client, that executes its content.
           In this particular code, a notification message is shown
        """
        code = """
            var options = {
                body: "%(content)s",
                icon: "%(icon)s"
            }
            if (!("Notification" in window)) {
                alert("%(content)s");
            }else if (Notification.permission === "granted") {
                var notification = new Notification("%(title)s", options);
            }else if (Notification.permission !== 'denied') {
                Notification.requestPermission(function (permission) {
                    if (permission === "granted") {
                        var notification = new Notification("%(title)s", options);
                    }
                });
            }
        """ % {'title': title, 'content': content, 'icon': icon}
        await self.execute_javascript(code)

    @abstractmethod
    def main(self):
        """implement here your gui..."""

    async def handle_get(self, stream, path, query, data, headers):

        # print(headers)

        if 'Upgrade' in headers:
            # print("UPGRADE stream!!!!")

            ws_handler = WebSocketHandler(self.cookie, headers, stream)

            async with trio.open_nursery() as nursery:

                nursery.start_soon(
                    ws_handler.handle, nursery)
            return

        path = str(unquote(path))
        async with self.update_lock:

            if not 'root' in self.page.children['body'].children.keys():
                self.logger.debug(f"built UI path={path}")
                self.set_root_widget(self.main())
        await self.process_all(stream, headers, path)

    async def handle_head(self, stream, path, query, data, headers):
        await self.send_response(stream, 200)
        await self.end_headers(stream)

    async def handle_post(self, stream, path, query, data, headers):
        file_data = None
        try:
            filename = headers['filename']
            listener_widget = runtimeInstances[headers['listener']]
            listener_function = headers['listener_function']
            for field in data.keys():
                field_item = data[field]
                if field_item.get('filename', None):
                    file_data = field_item['data']

                    get_method_by_name(listener_widget, listener_function)(file_data, filename)
        except KeyError:
            self._log.error("post: failed", exc_info=True)
            await self.send_response(stream, 400)
        await self.send_header(stream, 'Content-type', 'text/plain')
        await self.end_headers(stream)

        # print(data)

    def add_foreground_worker(self, worker):
        self.foreground_workers.append(worker)

    async def foreground_handler(self, nursery):
        pass

    async def check_started(self):
        if not self.started:
            self.started = True

            async with trio.open_nursery() as nursery:
                self.nursery = nursery
                nursery.start_soon(self.loop, nursery)

    async def loop(self, nursery):
        """main application loop"""
        self.active = True
        nursery.start_soon(self._idle_loop)
        nursery.start_soon(self.foreground_handler, nursery)
        while self.active:
            # self.logger.debug(f"Application[{str(id(self))}] active...")
            await trio.sleep(5)


class HttpRequestParser(object):

    def __init__(self, stream: trio.SocketStream):
        self.stream = stream
        self._application = None
        self.logger = logging.getLogger('remi.aserver.httpreqprsr')
        self._path = None
        self._query = None
        self._data = None
        self._method = None
        self._headers = None

    @property
    def application(self):
        return self._application

    def parse_raw_request(self, raw_request):
        headers = dict()

        raw_request, req_body = raw_request.split(b"\r\n\r\n", 1)

        try:
            request_line, headers_alone = raw_request.decode().split('\r\n', 1)
            headers_alone: str
            request_line: str
            method, path, proto = request_line.split(' ')
            self.logger.debug(f"meth[{method}], path[{path}]")
            self._path = path
            self._method = method
            for header in headers_alone.split("\r\n"):
                if len(header) < 3:
                    break
                name, value = header.split(": ", 1)
                headers.update({name: value})
            self._headers = headers

            # TODO: Handle POST ???
            # print(self._headers)

            content_len = self._headers.get("Content-Length", '-1')
            content_len = int(content_len)

            self.logger.debug(f'cl = {content_len}, req_body_len = {len(req_body)}')

            if self._method == "POST":
                if content_len == len(req_body):
                    self._data = req_body
                    return True
                else:
                    return False
            return True

        except Exception as e:
            # print(e)
            self.logger.error(str(e))
            return False

    def __getattribute__(self, item: str):
        if item.startswith('h_'):
            headers = object.__getattribute__(
                self, '_headers')
            if headers:
                return headers.get(item[2:], None)
        else:
            return object.__getattribute__(
                self, item)

    @property
    def headers(self):
        return self._headers

    @property
    def path(self):
        return self._path

    @property
    def method(self):
        return self._method

    @property
    def query(self):
        return self._query

    @property
    def data(self):
        return self._data

    async def parse_request(self):
        raw_request = b""

        while True:
            new_chunk = await self.stream.receive_some(
                2**16)
            if not new_chunk:
                break
            if len(new_chunk) == 0:
                break
            raw_request += new_chunk

            if self.parse_raw_request(raw_request):
                break

        # self.logger.debug(raw_request)
        self.logger.debug(f"req len = {len(raw_request)}")

        if self.method == "POST":
            self.handle_postpayload()

        # print("RH", self.headers)
        print("H_COOKIE", self.h_cookie, self.h_Cookie)
        
        if self.h_Cookie:
            application_cookie = self.h_Cookie.split("=")[-1]
            self.logger.debug(f"cookie = {application_cookie}")
            application_cookie = self.h_Cookie.split("=")[-1]
            application = ClientsManager().get(application_cookie)
            if not application:
                return None
            self._application = application
            return self.application
        else:
            return "nocookie"

    def handle_postpayload(self):

        data = dict()

        decoder = MultipartDecoder(
            content_type=self.headers['Content-Type'],
            content=self.data)

        for index, part in enumerate(decoder.parts):
            import requests_toolbelt.multipart.decoder
            part: requests_toolbelt.multipart.decoder.BodyPart
            # print(part)
            # print(part.headers)
            # print("CONTENT", type(part.content), len(part.content))
            filename = part.headers.get(b'Content-Disposition').decode().replace('"', '').rpartition('filename=')[2]
            data[index] = dict(filename=filename, data=part.content)

        self._data = data


class AuthFactory(object):

    def __init__(self):
        pass

    @abstractmethod
    async def get_user(self, headers):
        pass


class BasicAuthFactory(AuthFactory):

    def __init__(self):
        self.users = dict()

    async def get_user(self, headers):
        if not headers:
            return None
        if 'Authorization' in headers:
            try:
                encoded_auth: str = headers['Authorization'].rpartition(" ")[2]
                user_pass_pare: bytes = base64.decodestring(encoded_auth.encode())
                user, password = user_pass_pare.decode().split(":", 1)
                if user in self.users:
                    user = self.users.get(user, None)
                    # print(user, password)
                    if user and user['password'] == password:
                        return user
                    else:
                        return None
            except Exception as e:
                # print("EX", e)
                return None

    def is_admin(self, username):
        try:
            return self.users[username]['is_admin']
        except:
            return False

    def add_user(self, username=None, password=None, **credentials):
        self.users[username] = credentials
        self.users[username].update({'username': username, 'password': password})


class AServer(object):

    def __init__(self, cls_app: type, cls_http_request_parser: type = None, port: int = 33300, auth_factory: AuthFactory = None):
        self.cls_http_request_parser = cls_http_request_parser
        if not self.cls_http_request_parser:
            self.cls_http_request_parser = HttpRequestParser
        self.auth_factory = auth_factory
        if not self.auth_factory:
            self.auth_factory = BasicAuthFactory()
            self.auth_factory.add_user(username='test', password='test')
        self.cls_app = cls_app
        self.port = port
        self.logger = logging.getLogger('remi.aserver.AServer')
        self.logger.setLevel(logging.DEBUG)

    async def send_eof(self, stream):
        if isinstance(stream, trio.SocketStream):
            return await stream.send_eof()
        elif isinstance(stream, trio.SSLStream):
            return await stream.aclose()

    async def connection_handler(self, stream):
        try:
            return await self._connection_handler(stream)
        except Exception as e:
            self.logger.debug(str(e))
            logging.exception("message")

    async def _connection_handler(self, stream: trio.SocketStream):

        self.logger.debug("new connection")
        request_parser: HttpRequestParser = self.cls_http_request_parser(stream)
        application = await request_parser.parse_request()

        user = await self.auth_factory.get_user(request_parser.headers)

        self.logger.debug(f"app: {application}")
        self.logger.debug(f"user: {user}")

        if not user:
            # print(request_parser.headers)

            response = (
                "HTTP/1.1 401 OK",
                "WWW-Authenticate: Basic realm=\"Protected\"",
                "Content-type: text/html",
                "\r\n",
                "not authenticated",
            )
            response = ("\r\n".join(response)).encode()
            self.logger.debug(response)
            await stream.send_all(response)
            self.logger.debug("response was sent!")
            await self.send_eof(stream)
            return

        if not application:

            self.logger.debug(f"user = {user}")

            cookie = user['username']

            application: Application = ClientsManager().get(cookie)
            print(f"has application ???", application)

            if not application:
                application = await \
                    self.cls_app.create(
                        cookie,
                        stream,
                        headers=request_parser.headers,
                        server=self
                    )

            ClientsManager().add_client(cookie, application)
            response = (
                "HTTP/1.1 200 OK",
                f"Set-Cookie: cookie={cookie}",
                "\r\n"
            )
            await stream.send_all(
                ("\r\n".join(response)).encode()
            )
            await self.send_eof(stream)

            return
        elif application == "nocookie":
            self.logger.debug(f"user = {user}")

            cookie = user['username']

            application: Application = ClientsManager().get(cookie)
            print(f"has application ???", application)

            if not application:
                application = await \
                    self.cls_app.create(
                        cookie,
                        stream,
                        headers=request_parser.headers,
                        server=self
                    )

            ClientsManager().add_client(cookie, application)
            response = (
                "HTTP/1.1 200 OK",
                f"Set-Cookie: cookie={cookie}",
                "\r\n"
            )
            await stream.send_all(
                ("\r\n".join(response)).encode()
            )
            await self.send_eof(stream)

        await application.check_started()
        await application.handle_request(
            stream,
            request_parser.method,
            request_parser.path,
            request_parser.query,
            request_parser.data,
            request_parser.headers
        )

    async def run(self, key_file=None, cert_file=None):
        if not key_file and not cert_file:
            await trio.serve_tcp(self.connection_handler, self.port)
        else:

            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            ssl_context.load_cert_chain(cert_file, key_file)

            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.options |= (
                    ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_COMPRESSION
            )
            ssl_context.set_ciphers("ECDHE+AESGCM")
            ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
            ssl_ctx = ssl_context

            await trio.serve_ssl_over_tcp(
                self.connection_handler,
                self.port, ssl_context=ssl_ctx,
                https_compatible=False)


def start(app: AServer, key_file=None, cert_file=None):
    # print(app, key_file, cert_file, app.port)
    trio_asyncio.run(app.run, key_file, cert_file)


if __name__ == "__main__":

    class TestApp(Application):

        def on_button_click(self):
            print("BUTTON WAS CLICKED!!!")
            print(self.input.get_value())
            self.button.set_text(self.input.get_text())

        def on_data(self, w, data, filename):
            print(f"FILE {filename} was uploaded with size {len(data)}")

        async def foreground_handler(self, nursery):

            count = 0
            while True:
                count += 1
                await trio.sleep(30)
                await self.notification_message(
                    "Message",
                    f"Dummy message {count}")

        def main(self):
            container = gui.VBox(width="100%")
            container.append(gui.Label("Label1"))
            self.input = input = gui.TextInput()
            self.button = button = gui.Button('click me!')
            self.fileupload = gui.FileUploader(width="100%")
            self.fileupload.ondata.do(self.on_data)
            print(self.fileupload)

            button.onclick.do(lambda *args: self.on_button_click())
            container.append([input, button, self.fileupload])
            return container

    auth = BasicAuthFactory()
    auth.add_user(username='admin', password='password', is_admin=True)
    logging.basicConfig(level=logging.DEBUG)
    app = AServer(TestApp, HttpRequestParser, 9052, auth)

    print(len(sys.argv))
    if len(sys.argv) == 3:
        start(app, sys.argv[1], sys.argv[2])
    else:
        start(app)

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

import os
import sys
import logging
import functools
import threading
import collections
import inspect
try:
    import html
    escape = html.escape
except ImportError :
    import cgi
    escape = cgi.escape
import mimetypes
import base64
try:
    # Python 2.6-2.7
    from HTMLParser import HTMLParser
    h = HTMLParser()
    unescape = h.unescape
except ImportError:
    # Python 3
    try:
        from html.parser import HTMLParser
        h = HTMLParser()
        unescape = h.unescape
    except (ImportError, AttributeError):
        # Python 3.4+
        import html
        unescape = html.unescape

from .server import runtimeInstances
try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

log = logging.getLogger('remi.gui')

pyLessThan3 = sys.version_info < (3,)


def to_pix(x):
    return str(x) + 'px'


def from_pix(x):
    v = 0
    try:
        v = int(float(x.replace('px', '')))
    except ValueError:
        log.error('error parsing px', exc_info=True)
    return v


def jsonize(d):
    return ';'.join(map(lambda k, v: k + ':' + v + '', d.keys(), d.values()))


def load_resource(filename):
    """ Convenient function. Given a local path and filename (not in standard remi resource format),
        loads the content and returns a base64 encoded data.
        This method allows to bypass the remi resource file management, accessing directly local disk files.

        Args:
            filename (str): path and filename of a local file (ie. "/home/mydirectory/image.png")

        Returns:
            str: the encoded base64 data together with mimetype packed to be displayed immediately.
    """
    mimetype, encoding = mimetypes.guess_type(filename)
    data = ""
    with open(filename, 'rb') as f:
        data = f.read()
    data = base64.b64encode(data)
    if pyLessThan3:
        data = data.encode('utf-8')
    else:
        data = str(data, 'utf-8')
    return "data:%(mime)s;base64,%(data)s" % {'mime': mimetype, 'data': data}


def to_uri(uri_data):
    """ Convenient function to encase the resource filename or data in url('') keyword

        Args:
            uri_data (str): filename or base64 data of the resource file

        Returns:
            str: the input string encased in url('') ie. url('/res:image.png')
    """
    return ("url('%s')" % uri_data)


class CssStyleError(Exception):
    """
    Raised when (a combination of) settings will result in invalid/improper CSS
    """
    pass


class EventSource(object):
    def __init__(self, *args, **kwargs):
        self.setup_event_methods()

    def setup_event_methods(self):
        def a_method_not_builtin(obj):
            return hasattr(obj, '__is_event')
        for (method_name, method) in inspect.getmembers(self, predicate=a_method_not_builtin):
            # this is implicit in predicate
            #if not hasattr(method, '__is_event'):
            #    continue
            
            _event_info = None
            if hasattr(method, "_event_info"):
                _event_info = method._event_info

            e = ClassEventConnector(self, method_name, method)
            e._event_info = _event_info
            setattr(self, method_name, e)


class ClassEventConnector(object):
    """ This class allows to manage the events. Decorating a method with *decorate_event* decorator
        The method gets the __is_event flag. At runtime, the methods that has this flag gets replaced
        by a ClassEventConnector. This class overloads the __call__ method, where the event method is called,
        and after that the listener method is called too.
    """
    userdata = None
    kwuserdata = None
    callback = None
    def __init__(self, event_source_instance, event_name, event_method_bound):
        self.event_source_instance = event_source_instance
        self.event_name = event_name
        self.event_method_bound = event_method_bound
        self.connect = self.do  # for compatibility reasons

    def do(self, callback, *userdata, **kwuserdata):
        """ The callback and userdata gets stored, and if there is some javascript to add
            the js code is appended as attribute for the event source
        """
        self.userdata = userdata
        self.kwuserdata = kwuserdata

        if hasattr(self.event_method_bound, '_js_code'):
            js_stop_propagation = kwuserdata.pop('js_stop_propagation', False)
            js_prevent_default = kwuserdata.pop('js_prevent_default', False)
            self.event_source_instance.attributes[self.event_name] = self.event_method_bound._js_code % {
                'emitter_identifier': self.event_source_instance.identifier, 'event_name': self.event_name} + \
                ("event.stopPropagation();" if js_stop_propagation else "") + \
                ("event.preventDefault();" if js_prevent_default else "")
                
        self.callback = callback

    def __call__(self, *args, **kwargs):
        # here the event method gets called
        callback_params = self.event_method_bound(*args, **kwargs)
        if not self.callback:
            return callback_params

        if not self.userdata:
            self.userdata = ()
        if not self.kwuserdata:
            self.kwuserdata = {}
        if not callback_params:
            callback_params = self.userdata
        else:
            callback_params = callback_params + self.userdata
        # here the listener gets called, passing as parameters the return values of the event method
        # plus the userdata parameters
        return self.callback(self.event_source_instance, *callback_params, **self.kwuserdata)


def decorate_event(method):
    """ setup a method as an event """
    setattr(method, "__is_event", True )
    return method


def decorate_event_js(js_code):
    """setup a method as an event, adding also javascript code to generate

    Args:
        js_code (str): javascript code to generate the event client-side.
            js_code is added to the widget html as
            widget.attributes['onclick'] = js_code%{'emitter_identifier':widget.identifier, 'event_name':'onclick'}
    """
    def add_annotation(method):
        setattr(method, "__is_event", True)
        setattr(method, "_js_code", js_code)
        return method
    return add_annotation


def decorate_set_on_listener(prototype):
    """ Private decorator for use in the editor.
        Allows the Editor to create listener methods.

        Args:
            params (str): The list of parameters for the listener
                method (es. "(self, new_value)")
    """
    # noinspection PyDictCreation,PyProtectedMember
    def add_annotation(method):
        method._event_info = {'name':method.__name__, 'prototype':prototype}
        return method

    return add_annotation


def editor_attribute_decorator(group, description, _type, additional_data):
    def add_annotation(prop): 
        setattr(prop, "editor_attributes", {'description': description, 'type': _type, 'group': group, 'additional_data': additional_data})
        return prop
    return add_annotation


class _EventDictionary(dict, EventSource):
    """This dictionary allows to be notified if its content is changed.
    """
    changed = False
    def __init__(self, *args, **kwargs):
        super(_EventDictionary, self).__init__(*args, **kwargs)
        EventSource.__init__(self, *args, **kwargs)

    def __setitem__(self, key, value):
        if key in self:
            if self[key] == value:
                return
        ret = super(_EventDictionary, self).__setitem__(key, value)
        self.onchange()
        return ret

    def __delitem__(self, key):
        if key not in self:
            return
        ret = super(_EventDictionary, self).__delitem__(key)
        self.onchange()
        return ret

    def pop(self, key, d=None):
        if key not in self:
            return
        ret = super(_EventDictionary, self).pop(key, d)
        self.onchange()
        return ret

    def clear(self):
        ret = super(_EventDictionary, self).clear()
        self.onchange()
        return ret

    def update(self, d):
        ret = super(_EventDictionary, self).update(d)
        self.onchange()
        return ret

    def ischanged(self):
        return self.changed

    def align_version(self):
        self.changed = False

    @decorate_event
    def onchange(self):
        """Called on content change.
        """
        self.changed = True
        return ()


class Tag(object):
    """
    Tag is the base class of the framework. It represents an element that can be added to the GUI,
    but it is not necessarily graphically representable.
    """

    def __init__(self, attributes=None, _type='', _class=None,  **kwargs):
        """
        Args:
            attributes (dict): The attributes to be applied.
           _type (str): HTML element type or ''
           _class (str): CSS class or '' (defaults to Class.__name__)
           id (str): the unique identifier for the class instance, useful for public API definition.
        """
        if attributes is None:
            attributes = {}
        self._parent = None

        self.kwargs = kwargs

        self._render_children_list = []

        self.children = _EventDictionary()
        self.attributes = _EventDictionary()  # properties as class id style
        self.style = _EventDictionary()  # used by Widget, but instantiated here to make gui_updater simpler

        self.ignore_update = False
        self.refresh_enabled = True
        self.children.onchange.connect(self._need_update)
        self.attributes.onchange.connect(self._need_update)
        self.style.onchange.connect(self._need_update)

        self.type = _type
        self.identifier = str(id(self))

        # attribute['id'] can be overwritten to get a static Tag identifier
        self.attributes.update(attributes)

        # the runtime instances are processed every time a requests arrives, searching for the called method
        # if a class instance is not present in the runtimeInstances, it will
        # we not callable
        runtimeInstances[self.identifier] = self

        self.attr_class = self.__class__.__name__ if _class == None else _class

        # this variable will contain the repr of this tag, in order to avoid useless operations
        self._backup_repr = ''

    # @editor_attribute_decorator("Generic",'''The unique object identifier''', None, {})
    @property
    def identifier(self):
        return self.attributes['id']

    @identifier.setter
    def identifier(self, new_identifier):
        """Allows to set a unique id for the Tag.

        Args:
            new_identifier (str): a unique id for the tag
        """
        self.attributes['id'] = new_identifier
        runtimeInstances[new_identifier] = self

    def innerHTML(self, local_changed_widgets):
        ret = ''
        for k in self._render_children_list:
            s = self.children[k]
            if isinstance(s, Tag):
                ret = ret + s.repr(local_changed_widgets)
            elif isinstance(s, type('')):
                ret = ret + s
            elif isinstance(s, type(u'')):
                ret = ret + s.encode('utf-8')
            else:
                ret = ret + repr(s)
        return ret

    def repr(self, changed_widgets=None):
        """It is used to automatically represent the object to HTML format
        packs all the attributes, children and so on.

        Args:
            changed_widgets (dict): A dictionary containing a collection of tags that have to be updated.
                The tag that have to be updated is the key, and the value is its textual repr.
        """
        if changed_widgets is None:
            changed_widgets = {}
        local_changed_widgets = {}
        _innerHTML = self.innerHTML(local_changed_widgets)

        if self._ischanged() or (len(local_changed_widgets) > 0):
            self._backup_repr = ''.join(('<', self.type, ' ', self._repr_attributes, '>',
                                        _innerHTML, '</', self.type, '>'))
            # faster but unsupported before python3.6
            # self._backup_repr = f'<{self.type} {self._repr_attributes}>{_innerHTML}</{self.type}>'
        if self._ischanged():
            # if self changed, no matter about the children because will be updated the entire parent
            # and so local_changed_widgets is not merged
            changed_widgets[self] = self._backup_repr
            self._set_updated()
        else:
            changed_widgets.update(local_changed_widgets)
        return self._backup_repr

    def _need_update(self, emitter=None, child_ignore_update=False):
        # if there is an emitter, it means self is the actual changed widget
        if not emitter is None:
            tmp = dict(self.attributes)
            if len(self.style):
                tmp['style'] = jsonize(self.style)
            else:
                tmp.pop('style', None)
            self._repr_attributes = ' '.join('%s="%s"' % (k, v) if v is not None else k for k, v in
                                             tmp.items())
        if self.refresh_enabled:
            if self.get_parent():
                self.get_parent()._need_update(child_ignore_update = (self.ignore_update or child_ignore_update))

    def _ischanged(self):
        return self.children.changed or self.attributes.changed or self.style.changed

    def _set_updated(self):
        self.children.align_version()
        self.attributes.align_version()
        self.style.align_version()

    def disable_refresh(self):
        """ Prevents the parent widgets to be notified about an update. 
            This is required to improve performances in case of widgets updated 
                multiple times in a procedure.
        """
        self.refresh_enabled = False

    def enable_refresh(self):
        self.refresh_enabled = True

    def disable_update(self):
        """ Prevents clients updates. Remi will not send websockets update messages.
            The widgets are however iternally updated. So if the user updates the 
                webpage, the update is shown.
        """
        self.ignore_update = True

    def enable_update(self):
        self.ignore_update = False

    def add_class(self, cls):
        self.attributes['class'] = self.attributes['class'] + ' ' + cls

    def remove_class(self, cls):
        classes = [''] #as the result of split
        try:
            classes = self.attributes['class'].split(' ')
            classes.remove(cls)
        except ValueError:
            pass
        if len(classes) > 0:
            self.attributes['class'] = ' '.join(classes) if len(classes)>1 else classes[0]
        else:
            self.attributes['class'] = ''

    def add_child(self, key, value):
        """Adds a child to the Tag

        To retrieve the child call get_child or access to the Tag.children[key] dictionary.

        Args:
            key (str):  Unique child's identifier, or iterable of keys
            value (Tag, str): can be a Tag, an iterable of Tag or a str. In case of iterable
                of Tag is a dict, each item's key is set as 'key' param
        """
        if type(value) in (list, tuple, dict):
            if type(value) == dict:
                for k in value.keys():
                    self.add_child(k, value[k])
                return
            i = 0
            for child in value:
                self.add_child(key[i], child)
                i = i + 1
            return

        if hasattr(value, 'attributes'):
            value.attributes['data-parent-widget'] = self.identifier
            value._parent = self

        if key in self.children:
            self._render_children_list.remove(key)
        self._render_children_list.append(key)

        self.children[key] = value

    def get_child(self, key):
        """Returns the child identified by 'key'

        Args:
            key (str): Unique identifier of the child.
        """
        return self.children[key]

    def get_parent(self):
        """Returns the parent tag instance or None where not applicable
        """

        return self._parent

    def empty(self):
        """remove all children from the widget"""
        for k in list(self.children.keys()):
            self.remove_child(self.children[k])

    def remove_child(self, child):
        """Removes a child instance from the Tag's children.

        Args:
            child (Tag): The child to be removed.
        """
        if child in self.children.values() and hasattr(child, 'identifier'):
            for k in self.children.keys():
                if hasattr(self.children[k], 'identifier'):
                    if self.children[k].identifier == child.identifier:
                        if k in self._render_children_list:
                            self._render_children_list.remove(k)
                        self.children.pop(k)
                        # when the child is removed we stop the iteration
                        # this implies that a child replication should not be allowed
                        break


class Widget(Tag, EventSource):
    """ Base class for graphical gui widgets.
        A widget has a graphical css style and receives events from the webpage
    """
    # some constants for the events
    EVENT_ONCLICK = 'onclick'
    EVENT_ONDBLCLICK = 'ondblclick'
    EVENT_ONMOUSEDOWN = 'onmousedown'
    EVENT_ONMOUSEMOVE = 'onmousemove'
    EVENT_ONMOUSEOVER = 'onmouseover'
    EVENT_ONMOUSEOUT = 'onmouseout'
    EVENT_ONMOUSELEAVE = 'onmouseleave'
    EVENT_ONMOUSEUP = 'onmouseup'
    EVENT_ONTOUCHMOVE = 'ontouchmove'
    EVENT_ONTOUCHSTART = 'ontouchstart'
    EVENT_ONTOUCHEND = 'ontouchend'
    EVENT_ONTOUCHENTER = 'ontouchenter'
    EVENT_ONTOUCHLEAVE = 'ontouchleave'
    EVENT_ONTOUCHCANCEL = 'ontouchcancel'
    EVENT_ONKEYDOWN = 'onkeydown'
    EVENT_ONKEYPRESS = 'onkeypress'
    EVENT_ONKEYUP = 'onkeyup'
    EVENT_ONCHANGE = 'onchange'
    EVENT_ONINPUT = 'oninput'
    EVENT_ONFOCUS = 'onfocus'
    EVENT_ONBLUR = 'onblur'
    EVENT_ONCONTEXTMENU = "oncontextmenu"
    EVENT_ONUPDATE = 'onupdate'

    # None is not visible in editor
    @property
    @editor_attribute_decorator("Generic", '''The variable name used by the editor''', str, {})
    def variable_name(self): return self.__dict__.get('__variable_name', None)
    @variable_name.setter
    def variable_name(self, value): self.__dict__['__variable_name'] = value
    @variable_name.deleter
    def variable_name(self): del self.__dict__['__variable_name']

    @property
    @editor_attribute_decorator("Generic",'''The html class attribute, allows to assign a css style class. Multiple classes have to be separed by space.''', str, {})
    def attr_class(self): return self.attributes.get('class', None)
    @attr_class.setter
    def attr_class(self, value): self.attributes['class'] = value
    @attr_class.deleter
    def attr_class(self): del self.attributes['class']

    @property
    @editor_attribute_decorator("Generic",'''Defines if to overload the base class''', bool, {})
    def attr_editor_newclass(self): return self.__dict__.get('__editor_newclass', False)
    @attr_editor_newclass.setter
    def attr_editor_newclass(self, value): self.__dict__['__editor_newclass'] = value
    @attr_editor_newclass.deleter
    def attr_editor_newclass(self): del self.__dict__['__editor_newclass']

    @property
    @editor_attribute_decorator("Layout", '''CSS float.''', 'DropDown', {'possible_values': ('none', 'inherit ', 'left', 'right')})
    def css_float(self): return self.style.get('float', None)
    @css_float.setter
    def css_float(self, value): self.style['float'] = str(value)
    @css_float.deleter
    def css_float(self): del self.style['float']

    @property
    @editor_attribute_decorator("Geometry", '''Margins allows to define spacing aroung element''', str, {})
    def css_margin(self): return self.style.get('margin', None)
    @css_margin.setter
    def css_margin(self, value): self.style['margin'] = str(value)
    @css_margin.deleter
    def css_margin(self): del self.style['margin']

    @property
    @editor_attribute_decorator("Generic", '''Advisory information for the element''', str, {})
    def attr_title(self): return self.attributes.get('title', None)
    @attr_title.setter
    def attr_title(self, value): self.attributes['title'] = str(value)
    @attr_title.deleter
    def attr_title(self): del self.attributes['title']

    @property
    @editor_attribute_decorator("Generic", '''Specifies whether or not an element is visible.''', 'DropDown', {'possible_values': ('visible', 'hidden')})
    def css_visibility(self): return self.style.get('visibility', None)
    @css_visibility.setter
    def css_visibility(self, value): self.style['visibility'] = str(value)
    @css_visibility.deleter
    def css_visibility(self): del self.style['visibility']

    @property
    @editor_attribute_decorator("Geometry", '''Widget width.''', 'css_size', {})
    def css_width(self): return self.style.get('width', None)
    @css_width.setter
    def css_width(self, value): self.style['width'] = str(value)
    @css_width.deleter
    def css_width(self): del self.style['width']

    @property
    @editor_attribute_decorator("Geometry", '''Widget height.''', 'css_size', {})
    def css_height(self): return self.style.get('height', None)
    @css_height.setter
    def css_height(self, value): self.style['height'] = str(value)
    @css_height.deleter
    def css_height(self): del self.style['height']

    @property
    @editor_attribute_decorator("Geometry", '''Widget left.''', 'css_size', {})
    def css_left(self): return self.style.get('left', None)
    @css_left.setter
    def css_left(self, value): self.style['left'] = str(value)
    @css_left.deleter
    def css_left(self): del self.style['left']

    @property
    @editor_attribute_decorator("Geometry", '''Widget top.''', 'css_size', {})
    def css_top(self): return self.style.get('top', None)
    @css_top.setter
    def css_top(self, value): self.style['top'] = str(value)
    @css_top.deleter
    def css_top(self): del self.style['top']

    @property
    @editor_attribute_decorator("Geometry", '''Widget right.''', 'css_size', {})
    def css_right(self): return self.style.get('right', None)
    @css_right.setter
    def css_right(self, value): self.style['right'] = str(value)
    @css_right.deleter
    def css_right(self): del self.style['right']

    @property
    @editor_attribute_decorator("Geometry", '''Widget bottom.''', 'css_size', {})
    def css_bottom(self): return self.style.get('bottom', None)
    @css_bottom.setter
    def css_bottom(self, value): self.style['bottom'] = str(value)
    @css_bottom.deleter
    def css_bottom(self): del self.style['bottom']

    @property
    @editor_attribute_decorator("Geometry", '''Visibility behavior in case of content does not fit in size.''', 'DropDown', {'possible_values': ('visible', 'hidden', 'scroll', 'auto')})
    def css_overflow(self): return self.style.get('overflow', None)
    @css_overflow.setter
    def css_overflow(self, value): self.style['overflow'] = str(value)
    @css_overflow.deleter
    def css_overflow(self): del self.style['overflow']

    @property
    @editor_attribute_decorator("Background", '''Background color of the widget''', 'ColorPicker', {})
    def css_background_color(self): return self.style.get('background-color', None)
    @css_background_color.setter
    def css_background_color(self, value): self.style['background-color'] = str(value)
    @css_background_color.deleter
    def css_background_color(self): del self.style['background-color']

    @property
    @editor_attribute_decorator("Background", '''An optional background image''', 'url_editor', {})
    def css_background_image(self): return self.style.get('background-image', None)
    @css_background_image.setter
    def css_background_image(self, value): self.style['background-image'] = str(value)
    @css_background_image.deleter
    def css_background_image(self): del self.style['background-image']

    @property
    @editor_attribute_decorator("Background", '''The position of an optional background in the form 0% 0%''', str, {})
    def css_background_position(self): return self.style.get('background-position', None)
    @css_background_position.setter
    def css_background_position(self, value): self.style['background-position'] = str(value)
    @css_background_position.deleter
    def css_background_position(self): del self.style['background-position']

    @property
    @editor_attribute_decorator("Background", '''The repeat behaviour of an optional background image''', 'DropDown', {'possible_values': ('repeat', 'repeat-x', 'repeat-y', 'no-repeat', 'round', 'inherit')})
    def css_background_repeat(self): return self.style.get('background-repeat', None)
    @css_background_repeat.setter
    def css_background_repeat(self, value): self.style['background-repeat'] = str(value)
    @css_background_repeat.deleter
    def css_background_repeat(self): del self.style['background-repeat']

    @property
    @editor_attribute_decorator("Layout", '''The opacity property sets the opacity level for an element.
    The opacity-level describes the transparency-level, where 1 is not transparent at all, 0.5 is 50% see-through, and 0 is completely transparent.''', float, {'possible_values': '', 'min': 0.0, 'max': 1.0, 'default': 1.0, 'step': 0.1})
    def css_opacity(self): return self.style.get('opacity', None)
    @css_opacity.setter
    def css_opacity(self, value): self.style['opacity'] = str(value)
    @css_opacity.deleter
    def css_opacity(self): del self.style['opacity']

    @property
    @editor_attribute_decorator("Border", '''Border color''', 'ColorPicker', {})
    def css_border_color(self): return self.style.get('border-color', None)
    @css_border_color.setter
    def css_border_color(self, value): self.style['border-color'] = str(value)
    @css_border_color.deleter
    def css_border_color(self): del self.style['border-color']

    @property
    @editor_attribute_decorator("Border", '''Border thickness''', 'css_size', {})
    def css_border_width(self): return self.style.get('border-width', None)
    @css_border_width.setter
    def css_border_width(self, value): self.style['border-width'] = str(value)
    @css_border_width.deleter
    def css_border_width(self): del self.style['border-width']

    @property
    @editor_attribute_decorator("Border", '''Border thickness''', 'DropDown', {'possible_values': ('none', 'solid', 'dotted', 'dashed')})
    def css_border_style(self): return self.style.get('border-style', None)
    @css_border_style.setter
    def css_border_style(self, value): self.style['border-style'] = str(value)
    @css_border_style.deleter
    def css_border_style(self): del self.style['border-style']

    @property
    @editor_attribute_decorator("Border", '''Border rounding radius''', 'css_size', {})
    def css_border_radius(self): return self.style.get('border-radius', None)
    @css_border_radius.setter
    def css_border_radius(self, value): self.style['border-radius'] = str(value)
    @css_border_radius.deleter
    def css_border_radius(self): del self.style['border-radius']

    @property
    @editor_attribute_decorator("Font", '''Text color''', 'ColorPicker', {})
    def css_color(self): return self.style.get('color', None)
    @css_color.setter
    def css_color(self, value): self.style['color'] = str(value)
    @css_color.deleter
    def css_color(self): del self.style['color']

    @property
    @editor_attribute_decorator("Font", '''Font family name''', str, {})
    def css_font_family(self): return self.style.get('font-family', None)
    @css_font_family.setter
    def css_font_family(self, value): self.style['font-family'] = str(value)
    @css_font_family.deleter
    def css_font_family(self): del self.style['font-family']

    @property
    @editor_attribute_decorator("Font", '''Font size''', 'css_size', {})
    def css_font_size(self): return self.style.get('font-size', None)
    @css_font_size.setter
    def css_font_size(self, value): self.style['font-size'] = str(value)
    @css_font_size.deleter
    def css_font_size(self): del self.style['font-size']

    @property
    @editor_attribute_decorator("Font", '''The line height in pixels''', 'css_size', {})
    def css_line_height(self): return self.style.get('line-height', None)
    @css_line_height.setter
    def css_line_height(self, value): self.style['line-height'] = str(value)
    @css_line_height.deleter
    def css_line_height(self): del self.style['line-height']

    @property
    @editor_attribute_decorator("Font", '''Style''', 'DropDown', {'possible_values': ('normal', 'italic', 'oblique', 'inherit')})
    def css_font_style(self): return self.style.get('font-style', None)
    @css_font_style.setter
    def css_font_style(self, value): self.style['font-style'] = str(value)
    @css_font_style.deleter
    def css_font_style(self): del self.style['font-style']

    @property
    @editor_attribute_decorator("Font", '''Style''', 'DropDown', {'possible_values': ('normal', 'bold', 'bolder', 'lighter', '100', '200', '300', '400', '500', '600', '700', '800', '900', 'inherit')})
    def css_font_weight(self): return self.style.get('font-weight', None)
    @css_font_weight.setter
    def css_font_weight(self, value): self.style['font-weight'] = str(value)
    @css_font_weight.deleter
    def css_font_weight(self): del self.style['font-weight']

    @property
    @editor_attribute_decorator("Font", '''Specifies how white-space inside an element is handled''', 'DropDown', {'possible_values': ('normal', 'nowrap', 'pre', 'pre-line', 'pre-wrap', 'initial', 'inherit')})
    def css_white_space(self): return self.style.get('white-space', None)
    @css_white_space.setter
    def css_white_space(self, value): self.style['white-space'] = str(value)
    @css_white_space.deleter
    def css_white_space(self): del self.style['white-space']

    @property
    @editor_attribute_decorator("Font", '''Increases or decreases the space between characters in a text.''', 'css_size', {})
    def css_letter_spacing(self): return self.style.get('letter-spacing', None)
    @css_letter_spacing.setter
    def css_letter_spacing(self, value): self.style['letter-spacing'] = str(value)
    @css_letter_spacing.deleter
    def css_letter_spacing(self): del self.style['letter-spacing']

    @property
    @editor_attribute_decorator("Layout", '''The flex-direction property specifies the direction of the flexible items. Note: If the element is not a flexible item, the flex-direction property has no effect.''', 'DropDown', {'possible_values': ('row', 'row-reverse', 'column', 'column-reverse', 'initial', 'inherit')})
    def css_flex_direction(self): return self.style.get('flex-direction', None)
    @css_flex_direction.setter
    def css_flex_direction(self, value): self.style['flex-direction'] = str(value)
    @css_flex_direction.deleter
    def css_flex_direction(self): del self.style['flex-direction']

    @property
    @editor_attribute_decorator("Layout", '''The display property specifies the type of box used for an HTML element''', 'DropDown', {'possible_values': ('inline', 'block', 'contents', 'flex', 'grid', 'inline-block', 'inline-flex', 'inline-grid', 'inline-table', 'list-item', 'run-in', 'table', 'none', 'inherit')})
    def css_display(self): return self.style.get('display', None)
    @css_display.setter
    def css_display(self, value): self.style['display'] = str(value)
    @css_display.deleter
    def css_display(self): del self.style['display']

    @property
    @editor_attribute_decorator("Layout", '''The justify-content property aligns the flexible container's items when the items do not use all available space on the main-axis (horizontally)''', 'DropDown', {'possible_values': ('flex-start', 'flex-end', 'center', 'space-between', 'space-around', 'initial', 'inherit')})
    def css_justify_content(self): return self.style.get('justify-content', None)
    @css_justify_content.setter
    def css_justify_content(self, value): self.style['justify-content'] = str(value)
    @css_justify_content.deleter
    def css_justify_content(self): del self.style['justify-content']

    @property
    @editor_attribute_decorator("Layout", '''The align-items property specifies the default alignment for items inside the flexible container''', 'DropDown', {'possible_values': ('stretch', 'center', 'flex-start', 'flex-end', 'baseline', 'initial', 'inherit')})
    def css_align_items(self): return self.style.get('align-items', None)
    @css_align_items.setter
    def css_align_items(self, value): self.style['align-items'] = str(value)
    @css_align_items.deleter
    def css_align_items(self): del self.style['align-items']

    @property
    @editor_attribute_decorator("Layout", '''The flex-wrap property specifies whether the flexible items should wrap or not. Note: If the elements are not flexible items, the flex-wrap property has no effect''', 'DropDown', {'possible_values': ('nowrap', 'wrap', 'wrap-reverse', 'initial', 'inherit')})
    def css_flex_wrap(self): return self.style.get('flex-wrap', None)
    @css_flex_wrap.setter
    def css_flex_wrap(self, value): self.style['flex-wrap'] = str(value)
    @css_flex_wrap.deleter
    def css_flex_wrap(self): del self.style['flex-wrap']

    @property
    @editor_attribute_decorator("Layout", '''The align-content property modifies the behavior of the flex-wrap property.
    It is similar to align-items, but instead of aligning flex items, it aligns flex lines. Tip: Use the justify-content property to align the items on the main-axis (horizontally).Note: There must be multiple lines of items for this property to have any effect.''', 'DropDown', {'possible_values': ('stretch', 'center', 'flex-start', 'flex-end', 'space-between', 'space-around', 'initial', 'inherit')})
    def css_align_content(self): return self.style.get('align-content', None)
    @css_align_content.setter
    def css_align_content(self, value): self.style['align-content'] = str(value)
    @css_align_content.deleter
    def css_align_content(self): del self.style['align-content']

    @property
    @editor_attribute_decorator("Layout", '''The flex-flow property is a shorthand property for the flex-direction and the flex-wrap properties. The flex-direction property specifies the direction of the flexible items.''', 'DropDown', {'possible_values': ('flex-direction', 'flex-wrap', 'initial', 'inherit')})
    def css_flex_flow(self): return self.style.get('flex-flow', None)
    @css_flex_flow.setter
    def css_flex_flow(self, value): self.style['flex-flow'] = str(value)
    @css_flex_flow.deleter
    def css_flex_flow(self): del self.style['flex-flow']

    @property
    @editor_attribute_decorator("Layout", '''The order property specifies the order of a flexible item relative to the rest of the flexible items inside the same container. Note: If the element is not a flexible item, the order property has no effect.''', int, {'possible_values': '', 'min': -10000, 'max': 10000, 'default': 1, 'step': 1})
    def css_order(self): return self.style.get('order', None)
    @css_order.setter
    def css_order(self, value): self.style['order'] = str(value)
    @css_order.deleter
    def css_order(self): del self.style['order']

    @property
    @editor_attribute_decorator("Layout", '''The align-self property specifies the alignment for the selected item inside the flexible container. Note: The align-self property overrides the flexible container's align-items property''', 'DropDown', {'possible_values': ('auto', 'stretch', 'center', 'flex-start', 'flex-end', 'baseline', 'initial', 'inherit')})
    def css_align_self(self): return self.style.get('align-self', None)
    @css_align_self.setter
    def css_align_self(self, value): self.style['align-self'] = str(value)
    @css_align_self.deleter
    def css_align_self(self): del self.style['align-self']

    @property
    @editor_attribute_decorator("Layout", '''The flex property specifies the length of the item, relative to the rest of the flexible items inside the same container. The flex property is a shorthand for the flex-grow, flex-shrink, and the flex-basis properties. Note: If the element is not a flexible item, the flex property has no effect.''', int, {'possible_values': '', 'min': -10000, 'max': 10000, 'default': 1, 'step': 1})
    def css_flex(self): return self.style.get('flex', None)
    @css_flex.setter
    def css_flex(self, value): self.style['flex'] = str(value)
    @css_flex.deleter
    def css_flex(self): del self.style['flex']

    @property
    @editor_attribute_decorator("Layout", '''The position property specifies the type of positioning method used for an element.''', 'DropDown', {'possible_values': ('static', 'absolute', 'fixed', 'relative', 'initial', 'inherit')})
    def css_position(self): return self.style.get('position', None)
    @css_position.setter
    def css_position(self, value): self.style['position'] = str(value)
    @css_position.deleter
    def css_position(self): del self.style['position']

    def __init__(self, style=None, *args, **kwargs):

        """
        Args:
            style (dict, or json str): The style properties to be applied.
            width (int, str): An optional width for the widget (es. width=10 or width='10px' or width='10%').
            height (int, str): An optional height for the widget (es. height=10 or height='10px' or height='10%').
            margin (str): CSS margin specifier
        """
        if style is None:
            style = {}
        if '_type' not in kwargs:
            kwargs['_type'] = 'div'

        super(Widget, self).__init__(**kwargs)
        EventSource.__init__(self, *args, **kwargs)

        self.oldRootWidget = None  # used when hiding the widget

        if 'margin' in kwargs:
            self.css_margin = kwargs.get('margin')
        self.set_size(kwargs.get('width'), kwargs.get('height'))
        self.set_style(style)

    def set_style(self, style):
        """ Allows to set style properties for the widget.
            Args:
                style (str or dict): The style property dictionary or json string.
        """
        if style is not None:
            try:
                self.style.update(style)
            except ValueError:
                for s in style.split(';'):
                    k, v = s.split(':', 1)
                    self.style[k.strip()] = v.strip()

    def set_enabled(self, enabled):
        """ Sets the enabled status. 
            If a widget is disabled the user iteraction is not allowed

            Args:
                enabled(bool) : the enabling flag
        """
        if enabled:
            try:
                del self.attributes['disabled']
            except KeyError:
                pass
        else:
            self.attributes['disabled'] = 'True'

    def get_enabled(self):
        """ Returns a bool.
        """
        return not ('disabled' in self.attributes.keys())

    def set_size(self, width, height):
        """Set the widget size.

        Args:
            width (int or str): An optional width for the widget (es. width=10 or width='10px' or width='10%').
            height (int or str): An optional height for the widget (es. height=10 or height='10px' or height='10%').
        """
        if width is not None:
            try:
                width = to_pix(int(width))
            except ValueError:
                # now we know w has 'px or % in it'
                pass
            self.css_width = width

        if height is not None:
            try:
                height = to_pix(int(height))
            except ValueError:
                # now we know w has 'px or % in it'
                pass
            self.css_height = height

    def redraw(self):
        """Forces a graphic update of the widget"""
        self._need_update()

    def repr(self, changed_widgets=None):
        """Represents the widget as HTML format, packs all the attributes, children and so on.

        Args:
            changed_widgets (dict): A dictionary containing a collection of widgets that have to be updated.
                The Widget that have to be updated is the key, and the value is its textual repr.
        """
        if changed_widgets is None:
            changed_widgets = {}
        return super(Widget, self).repr(changed_widgets)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onfocus(self):
        """Called when the Widget gets focus."""
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onblur(self):
        """Called when the Widget loses focus"""
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onclick(self):
        """Called when the Widget gets clicked by the user with the left mouse button."""
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def ondblclick(self):
        """Called when the Widget gets double clicked by the user with the left mouse button."""
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def oncontextmenu(self):
        """Called when the Widget gets clicked by the user with the right mouse button.
        """
        return ()

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=event.clientX-boundingBox.left;" \
            "params['y']=event.clientY-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def onmousedown(self, x, y):
        """Called when the user presses left or right mouse button over a Widget.

        Args:
            x (float): position of the mouse inside the widget
            y (float): position of the mouse inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=event.clientX-boundingBox.left;" \
            "params['y']=event.clientY-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def onmouseup(self, x, y):
        """Called when the user releases left or right mouse button over a Widget.

        Args:
            x (float): position of the mouse inside the widget
            y (float): position of the mouse inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onmouseout(self):
        """Called when the mouse cursor moves outside a Widget.

        Note: This event is often used together with the Widget.onmouseover event, which occurs when the pointer is
            moved onto a Widget, or onto one of its children.
        """
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onmouseover(self):
        """Called when the mouse cursor moves onto a Widget.

        Note: This event is often used together with the Widget.onmouseout event.
        """
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onmouseleave(self):
        """Called when the mouse cursor moves outside a Widget.

        Note: This event is often used together with the Widget.onmouseenter event, which occurs when the mouse pointer
            is moved onto a Widget.

        Note: The Widget.onmouseleave event is similar to the Widget.onmouseout event. The only difference is that the
            onmouseleave event does not bubble (does not propagate up the Widgets tree).
        """
        return ()

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=event.clientX-boundingBox.left;" \
            "params['y']=event.clientY-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def onmousemove(self, x, y):
        """Called when the mouse cursor moves inside the Widget.

        Args:
            x (float): position of the mouse inside the widget
            y (float): position of the mouse inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=parseInt(event.changedTouches[0].clientX)-boundingBox.left;" \
            "params['y']=parseInt(event.changedTouches[0].clientY)-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def ontouchmove(self, x, y):
        """Called continuously while a finger is dragged across the screen, over a Widget.

        Args:
            x (float): position of the finger inside the widget
            y (float): position of the finger inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=parseInt(event.changedTouches[0].clientX)-boundingBox.left;" \
            "params['y']=parseInt(event.changedTouches[0].clientY)-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def ontouchstart(self, x, y):
        """Called when a finger touches the widget.

        Args:
            x (float): position of the finger inside the widget
            y (float): position of the finger inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=parseInt(event.changedTouches[0].clientX)-boundingBox.left;" \
            "params['y']=parseInt(event.changedTouches[0].clientY)-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def ontouchend(self, x, y):
        """Called when a finger is released from the widget.

        Args:
            x (float): position of the finger inside the widget
            y (float): position of the finger inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter, x, y)")
    @decorate_event_js("var params={};" \
            "var boundingBox = this.getBoundingClientRect();" \
            "params['x']=parseInt(event.changedTouches[0].clientX)-boundingBox.left;" \
            "params['y']=parseInt(event.changedTouches[0].clientY)-boundingBox.top;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);")
    def ontouchenter(self, x, y):
        """Called when a finger touches from outside to inside the widget.

        Args:
            x (float): position of the finger inside the widget
            y (float): position of the finger inside the widget
        """
        return (x, y)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def ontouchleave(self):
        """Called when a finger touches from inside to outside the widget.
        """
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def ontouchcancel(self):
        """Called when a touch point has been disrupted in an implementation-specific manner
        (for example, too many touch points are created).
        """
        return ()

    @decorate_set_on_listener("(self, emitter, key, keycode, ctrl, shift, alt)")
    @decorate_event_js("""var params={};params['key']=event.key;
            params['keycode']=(event.which||event.keyCode);
            params['ctrl']=event.ctrlKey;
            params['shift']=event.shiftKey;
            params['alt']=event.altKey;
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onkeyup(self, key, keycode, ctrl, shift, alt):
        """Called when user types and releases a key.
        The widget should be able to receive the focus in order to emit the event.
        Assign a 'tabindex' attribute to make it focusable.

        Args:
            key (str): the character value
            keycode (str): the numeric char code
        """
        return (key, keycode, ctrl, shift, alt)

    @decorate_set_on_listener("(self, emitter, key, keycode, ctrl, shift, alt)")
    @decorate_event_js("""var params={};params['key']=event.key;
            params['keycode']=(event.which||event.keyCode);
            params['ctrl']=event.ctrlKey;
            params['shift']=event.shiftKey;
            params['alt']=event.altKey;
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onkeydown(self, key, keycode, ctrl, shift, alt):
        """Called when user types and releases a key.
        The widget should be able to receive the focus in order to emit the event.
        Assign a 'tabindex' attribute to make it focusable.

        Args:
            key (str): the character value
            keycode (str): the numeric char code
        """
        return (key, keycode, ctrl, shift, alt)


    def query_client(self, app_instance, attribute_list, style_property_list):
        """
            WARNING: this is a new feature, subject to changes.
            This method allows to query client rendering attributes and style properties of a widget.
            The user, in order to get back the values must register a listener for the event 'onquery_client_result'.
            Args:
                app_instance (App): the app instance
                attribute_list (list): list of attributes names
                style_property_list (list): list of style property names
        """
        app_instance.execute_javascript("""
                var params={};
                %(attributes)s
                %(style)s
                remi.sendCallbackParam('%(emitter_identifier)s','%(callback_name)s',params);
            """ % {
                    'attributes': ";".join(map(lambda param_name: "params['%(param_name)s']=document.getElementById('%(emitter_identifier)s').%(param_name)s" % {'param_name': param_name, 'emitter_identifier': str(self.identifier)}, attribute_list)),
                    'style': ";".join(map(lambda param_name: "params['%(param_name)s']=document.getElementById('%(emitter_identifier)s').style.%(param_name)s" % {'param_name': param_name, 'emitter_identifier': str(self.identifier)}, style_property_list)),
                    'emitter_identifier': str(self.identifier),
                    'callback_name': 'onquery_client_result'
                }
            )

    @decorate_set_on_listener("(self, emitter, values_dictionary)")
    @decorate_event
    def onquery_client_result(self, **kwargs):
        """ WARNING: this is a new feature, subject to changes.
            This event allows to get back the values fetched by 'query' method.
            Returns:
                values_dictionary (dict): a dictionary containing name:value of all the requested parameters
        """
        return (kwargs,)


class Container(Widget):
    """
    Container can be used as generic container. You can add children by the append(value, key) function.
    Container can be arranged in absolute positioning (assigning style['top'] and style['left'] attributes to the children
    or in a simple auto-alignment.
    You can decide the horizontal or vertical arrangement by the function set_layout_orientation(layout_orientation)
    passing as parameter Container.LAYOUT_HORIZONTAL or Container.LAYOUT_VERTICAL.

    Tips:
    In html, it is a DIV tag
    The self.type attribute specifies the HTML tag representation
    The self.attributes[] attribute specifies the HTML attributes like 'style' 'class' 'id'
    The self.style[] attribute specifies the CSS style content like 'font' 'color'. It will be packed together with
    'self.attributes'.
    """

    # constants
    LAYOUT_HORIZONTAL = True
    LAYOUT_VERTICAL = False

    def __init__(self, children=None, *args, **kwargs):
        """
        Args:
            children (Widget, or iterable of Widgets): The child to be appended. In case of a dictionary,
                each item's key is used as 'key' param for the single append.
            layout_orientation (Container.LAYOUT_VERTICAL, Container.LAYOUT_HORIZONTAL): Container layout
        """
        super(Container, self).__init__(*args, **kwargs)

        self.set_layout_orientation(kwargs.get('layout_orientation', Container.LAYOUT_VERTICAL))
        if children:
            self.append(children)

    def append(self, value, key=''):
        """Adds a child widget, generating and returning a key if not provided

        In order to access to the specific child in this way container.children[key].

        Args:
            value (Widget, or iterable of Widgets): The child to be appended. In case of a dictionary,
                each item's key is used as 'key' param for the single append.
            key (str): The unique string identifier for the child. Ignored in case of iterable 'value'
                param.

        Returns:
            str: a key used to refer to the child for all future interaction, or a list of keys in case
                of an iterable 'value' param
        """
        if type(value) in (list, tuple, dict):
            if type(value) == dict:
                for k in value.keys():
                    self.append(value[k], k)
                return value.keys()
            keys = []
            for child in value:
                keys.append(self.append(child))
            return keys

        if not isinstance(value, Widget):
            raise ValueError('value should be a Widget (otherwise use add_child(key,other)')

        key = value.identifier if key == '' else key
        self.add_child(key, value)

        if self.layout_orientation == Container.LAYOUT_HORIZONTAL:
            if not self.children[key].css_float is None:
                if not (self.children[key].css_float == 'none'):
                    self.children[key].css_float = 'left'
            else:
                self.children[key].css_float = 'left'

        return key

    def set_layout_orientation(self, layout_orientation):
        """For the generic Container, this function allows to setup the children arrangement.

        Args:
            layout_orientation (Container.LAYOUT_HORIZONTAL or Container.LAYOUT_VERTICAL):
        """
        self.layout_orientation = layout_orientation


class HTML(Tag):
    def __init__(self, *args, **kwargs):
        super(HTML, self).__init__(*args, _type='html', **kwargs)

    def repr(self, changed_widgets=None):
        """It is used to automatically represent the object to HTML format
        packs all the attributes, children and so on.

        Args:
            changed_widgets (dict): A dictionary containing a collection of tags that have to be updated.
                The tag that have to be updated is the key, and the value is its textual repr.
        """
        if changed_widgets is None:
            changed_widgets = {}
        local_changed_widgets = {}
        self._set_updated()
        return ''.join(('<', self.type, '>\n', self.innerHTML(local_changed_widgets), '\n</', self.type, '>'))


class HEAD(Tag):
    def __init__(self, title, *args, **kwargs):
        super(HEAD, self).__init__(*args, _type='head', **kwargs)
        self.add_child('meta',
                """<meta content='text/html;charset=utf-8' http-equiv='Content-Type'>
                <meta content='utf-8' http-equiv='encoding'>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">""")

        self.set_title(title)

    def set_icon_file(self, filename, rel="icon"):
        """ Allows to define an icon for the App

            Args:
                filename (str): the resource file name (ie. "/res:myicon.png")
                rel (str): leave it unchanged (standard "icon")
        """
        mimetype, encoding = mimetypes.guess_type(filename)
        self.add_child("favicon", '<link rel="%s" href="%s" type="%s" />'%(rel, filename, mimetype))

    def set_icon_data(self, base64_data, mimetype="image/png", rel="icon"):
        """ Allows to define an icon for the App

            Args:
                base64_data (str): base64 encoded image data  (ie. "data:image/x-icon;base64,AAABAAEAEBA....")
                mimetype (str): mimetype of the image ("image/png" or "image/x-icon"...)
                rel (str): leave it unchanged (standard "icon")
        """
        self.add_child("favicon", '<link rel="%s" href="%s" type="%s" />'%(rel, base64_data, mimetype))

    def set_internal_js(self, app_identifier, net_interface_ip, pending_messages_queue_length, websocket_timeout_timer_ms):
        self.add_child('internal_js',
                """
                <script>
                /*'use strict';*/

                var Remi = function() {
                this._pendingSendMessages = [];
                this._ws = null;
                this._comTimeout = null;
                this._failedConnections = 0;
                this._openSocket();
                };

                // from http://stackoverflow.com/questions/5515869/string-length-in-bytes-in-javascript
                // using UTF8 strings I noticed that the javascript .length of a string returned less
                // characters than they actually were
                Remi.prototype._byteLength = function(str) {
                    // returns the byte length of an utf8 string
                    return str.length;
                };

                Remi.prototype._paramPacketize = function (ps){
                    var ret = '';
                    for (var pkey in ps) {
                        if( ret.length>0 )ret = ret + '|';
                        var pstring = pkey+'='+ps[pkey];
                        var pstring_length = this._byteLength(pstring);
                        pstring = pstring_length+'|'+pstring;
                        ret = ret + pstring;
                    }
                    return ret;
                };

                Remi.prototype._openSocket = function(){
                    var ws_wss = "ws";
                    try{
                        ws_wss = document.location.protocol.startsWith('https')?'wss':'ws';
                    }catch(ex){}

                    var self = this;
                    try{
                        this._ws = new WebSocket(ws_wss + '://%(host)s/');
                        console.debug('opening websocket');

                        this._ws.onopen = function(evt){
                            if(self._ws.readyState == 1){
                                self._ws.send('connected');

                                try {
                                    document.getElementById("loading").style.display = 'none';
                                } catch(err) {
                                    console.log('Error hiding loading overlay ' + err.message);
                                }

                                self._failedConnections = 0;

                                while(self._pendingSendMessages.length>0){
                                    self._ws.send(self._pendingSendMessages.shift()); /*without checking ack*/
                                }
                            }
                            else{
                                console.debug('onopen fired but the socket readyState was not 1');
                            }
                        };

                        this._ws.onmessage = function(evt){
                            var received_msg = evt.data;

                            if( received_msg[0]=='0' ){ /*show_window*/
                                var index = received_msg.indexOf(',')+1;
                                /*var idRootNodeWidget = received_msg.substr(0,index-1);*/
                                var content = received_msg.substr(index,received_msg.length-index);

                                document.body.innerHTML = decodeURIComponent(content);
                            }else if( received_msg[0]=='1' ){ /*update_widget*/
                                var focusedElement=-1;
                                var caretStart=-1;
                                var caretEnd=-1;
                                if (document.activeElement)
                                {
                                    focusedElement = document.activeElement.id;
                                    try{
                                        caretStart = document.activeElement.selectionStart;
                                        caretEnd = document.activeElement.selectionEnd;
                                    }catch(e){console.debug(e.message);}
                                }
                                var index = received_msg.indexOf(',')+1;
                                var idElem = received_msg.substr(1,index-2);
                                var content = received_msg.substr(index,received_msg.length-index);

                                var elem = document.getElementById(idElem);
                                try{
                                    elem.insertAdjacentHTML('afterend',decodeURIComponent(content));
                                    elem.parentElement.removeChild(elem);
                                }catch(e){
                                    /*Microsoft EDGE doesn't support insertAdjacentHTML for SVGElement*/
                                    var ns = document.createElementNS("http://www.w3.org/2000/svg",'tmp');
                                    ns.innerHTML = decodeURIComponent(content);
                                    elem.parentElement.replaceChild(ns.firstChild, elem);
                                    console.debug(e.message);
                                }

                                var elemToFocus = document.getElementById(focusedElement);
                                if( elemToFocus != null ){
                                    elemToFocus.focus();
                                    try{
                                        elemToFocus = document.getElementById(focusedElement);
                                        if(caretStart>-1 && caretEnd>-1) elemToFocus.setSelectionRange(caretStart, caretEnd);
                                    }catch(e){console.debug(e.message);}
                                }
                            }else if( received_msg[0]=='2' ){ /*javascript*/
                                var content = received_msg.substr(1,received_msg.length-1);
                                try{
                                    eval(content);
                                }catch(e){console.debug(e.message);};
                            }else if( received_msg[0]=='3' ){ /*ack*/
                                self._pendingSendMessages.shift() /*remove the oldest*/
                                if(self._comTimeout!==null)
                                    clearTimeout(self._comTimeout);
                            }
                        };

                        this._ws.onclose = function(evt){
                            /* websocket is closed. */
                            console.debug('Connection is closed... event code: ' + evt.code + ', reason: ' + evt.reason);
                            // Some explanation on this error: http://stackoverflow.com/questions/19304157/getting-the-reason-why-websockets-closed
                            // In practice, on a unstable network (wifi with a lot of traffic for example) this error appears
                            // Got it with Chrome saying:
                            // WebSocket connection to 'ws://x.x.x.x:y/' failed: Could not decode a text frame as UTF-8.
                            // WebSocket connection to 'ws://x.x.x.x:y/' failed: Invalid frame header

                            try {
                                document.getElementById("loading").style.display = '';
                            } catch(err) {
                                console.log('Error hiding loading overlay ' + err.message);
                            }

                            self._failedConnections += 1;

                            console.debug('failed connections=' + self._failedConnections + ' queued messages=' + self._pendingSendMessages.length);

                            if(self._failedConnections > 3) {

                                // check if the server has been restarted - which would give it a new websocket address,
                                // new state, and require a reload
                                console.debug('Checking if GUI still up ' + location.href);

                                var http = new XMLHttpRequest();
                                http.open('HEAD', location.href);
                                http.onreadystatechange = function() {
                                    if (http.status == 200) {
                                        // server is up but has a new websocket address, reload
                                        location.reload();
                                    }
                                };
                                http.send();

                                self._failedConnections = 0;
                            }

                            if(evt.code == 1006){
                                self._renewConnection();
                            }
                        };

                        this._ws.onerror = function(evt){
                            /* websocket is closed. */
                            /* alert('Websocket error...');*/
                            console.debug('Websocket error... event code: ' + evt.code + ', reason: ' + evt.reason);
                        };

                    }catch(ex){this._ws=false;alert('websocketnot supported or server unreachable');}
                }


                /*this uses websockets*/
                Remi.prototype.sendCallbackParam = function (widgetID,functionName,params /*a dictionary of name:value*/){
                    var paramStr = '';
                    if(params!==null) paramStr=this._paramPacketize(params);
                    var message = encodeURIComponent(unescape('callback' + '/' + widgetID+'/'+functionName + '/' + paramStr));
                    this._pendingSendMessages.push(message);
                    if( this._pendingSendMessages.length < %(max_pending_messages)s ){
                        if (this._ws !== null && this._ws.readyState == 1)
                            this._ws.send(message);
                            if(this._comTimeout===null)
                                this._comTimeout = setTimeout(this._checkTimeout, %(messaging_timeout)s);
                    }else{
                        console.debug('Renewing connection, this._ws.readyState when trying to send was: ' + this._ws.readyState)
                        this._renewConnection();
                    }
                };

                /*this uses websockets*/
                Remi.prototype.sendCallback = function (widgetID,functionName){
                    this.sendCallbackParam(widgetID,functionName,null);
                };

                Remi.prototype._renewConnection = function(){
                    // ws.readyState:
                    //A value of 0 indicates that the connection has not yet been established.
                    //A value of 1 indicates that the connection is established and communication is possible.
                    //A value of 2 indicates that the connection is going through the closing handshake.
                    //A value of 3 indicates that the connection has been closed or could not be opened.
                    if( this._ws.readyState == 1){
                        try{
                            this._ws.close();
                        }catch(err){};
                    }
                    else if(this._ws.readyState == 0){
                    // Don't do anything, just wait for the connection to be stablished
                    }
                    else{
                        this._openSocket();
                    }
                };

                Remi.prototype._checkTimeout = function(){
                    if(this._pendingSendMessages.length > 0)
                        this._renewConnection();
                };

                Remi.prototype.uploadFile = function(widgetID, eventSuccess, eventFail, eventData, file){
                    var url = '/';
                    var xhr = new XMLHttpRequest();
                    xhr.upload.addEventListener('progress', function(e) {
                        console.log('progress!', widgetID, %(emitter_identifier)s, e.loaded, e.total);
                        if(event.lengthComputable){
                            var params={};
                            params['filename'] = 'filename'/* file.name*/;
                            params['loaded'] = event.loaded;
                            params['total'] = event.total;
                            console.log("length is computable; sending callback");
                            remi.sendCallbackParam(widgetID,'onprogress',params);
                        }
                    });

                    var fd = new FormData();
                    xhr.open('POST', url, true);
                    xhr.setRequestHeader('filename', file.name);
                    xhr.setRequestHeader('listener', widgetID);
                    xhr.setRequestHeader('listener_function', eventData);
                    xhr.onreadystatechange = function() {
                        if (xhr.readyState == 4 && xhr.status == 200) {
                            /* Every thing ok, file uploaded */
                            var params={};params['filename']=file.name;
                            remi.sendCallbackParam(widgetID, eventSuccess,params);
                            console.log('upload success: ' + file.name);
                        }else if(xhr.status == 400){
                            var params={};params['filename']=file.name;
                            remi.sendCallbackParam(widgetID,eventFail,params);
                            console.log('upload failed: ' + file.name);
                        }
                    };

                    fd.append('upload_file', file);
                    xhr.send(fd);
                };

                window.onerror = function(message, source, lineno, colno, error) {
                    var params={};params['message']=message;
                    params['source']=source;
                    params['lineno']=lineno;
                    params['colno']=colno;
                    params['error']=JSON.stringify(error);
                    remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);
                    return false;
                };
                
                window.remi = new Remi();

                </script>""" % {'host':net_interface_ip,
                                'max_pending_messages':pending_messages_queue_length,
                                'messaging_timeout':websocket_timeout_timer_ms,
                                'emitter_identifier':app_identifier,
                                'event_name':'onerror'})

    def set_title(self, title):
        self.add_child('title', "<title>%s</title>" % title)

    def repr(self, changed_widgets=None):
        """It is used to automatically represent the object to HTML format
        packs all the attributes, children and so on.

        Args:
            changed_widgets (dict): A dictionary containing a collection of tags that have to be updated.
                The tag that have to be updated is the key, and the value is its textual repr.
        """
        if changed_widgets is None:
            changed_widgets = {}
        local_changed_widgets = {}
        self._set_updated()
        return ''.join(('<', self.type, '>\n', self.innerHTML(local_changed_widgets), '\n</', self.type, '>'))


class BODY(Container):
    EVENT_ONLOAD = 'onload'
    EVENT_ONERROR = 'onerror'
    EVENT_ONONLINE = 'ononline'
    EVENT_ONPAGEHIDE = 'onpagehide'
    EVENT_ONPAGESHOW = 'onpageshow'
    EVENT_ONRESIZE = 'onresize'

    def __init__(self, *args, **kwargs):
        super(BODY, self).__init__(*args, _type='body', **kwargs)
        loading_anim = Widget()
        loading_anim.css_margin = None
        loading_anim.identifier = "loading-animation"
        loading_container = Container(children=[loading_anim], style={'display':'none'})
        loading_container.css_margin = None
        loading_container.identifier = "loading"

        self.append(loading_container)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("""remi.sendCallback('%(emitter_identifier)s','%(event_name)s');""")
    def onload(self):
        """Called when page gets loaded."""
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("""remi.sendCallback('%(emitter_identifier)s','%(event_name)s');""")
    def ononline(self):
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("""remi.sendCallback('%(emitter_identifier)s','%(event_name)s');""")
    def onpagehide(self):
        return ()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("""
            var params={};
            params['width']=window.innerWidth;
            params['height']=window.innerHeight;
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onpageshow(self, width, height):
        return (width, height)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("""
            var params={};
            params['width']=window.innerWidth;
            params['height']=window.innerHeight;
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onresize(self, width, height):
        return (width, height)


class GridBox(Container):
    """It contains widgets automatically aligning them to the grid.
    Does not permit children absolute positioning.

    In order to add children to this container, use the append(child, key) function.
    The key have to be string and determines the children positioning in the layout.

    Note: If you would absolute positioning, use the Container instead.
    """

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Column sizes (i.e. 50% 30% 20%).''', str, {})
    def css_grid_template_columns(self): return self.style.get('grid-template-columns', None)
    @css_grid_template_columns.setter
    def css_grid_template_columns(self, value): self.style['grid-template-columns'] = str(value)
    @css_grid_template_columns.deleter
    def css_grid_template_columns(self): del self.style['grid-template-columns']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Row sizes (i.e. 50% 30% 20%).''', str, {})
    def css_grid_template_rows(self): return self.style.get('grid-template-rows', None)
    @css_grid_template_rows.setter
    def css_grid_template_rows(self, value): self.style['grid-template-rows'] = str(value)
    @css_grid_template_rows.deleter
    def css_grid_template_rows(self): del self.style['grid-template-rows']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Grid matrix (i.e. 'widget1 widget1 widget2' 'widget1 widget1 widget2').''', str, {})
    def css_grid_template_areas(self): return self.style.get('grid-template-areas', None)
    @css_grid_template_areas.setter
    def css_grid_template_areas(self, value): self.style['grid-template-areas'] = str(value)
    @css_grid_template_areas.deleter
    def css_grid_template_areas(self): del self.style['grid-template-areas']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the size of the gap between the rows and columns.''', 'css_size', {})
    def css_grid_gap(self): return self.style.get('grid-gap', None)
    @css_grid_gap.setter
    def css_grid_gap(self, value): self.style['grid-gap'] = str(value)
    @css_grid_gap.deleter
    def css_grid_gap(self): del self.style['grid-gap']

    def __init__(self, *args, **kwargs):
        super(GridBox, self).__init__(*args, **kwargs)
        self.style.update({'display': 'grid'})

    def define_grid(self, matrix):
        """Populates the Table with a list of tuples of strings.

        Args:
            matrix (list): list of iterables of strings (lists or something else).
                Items in the matrix have to correspond to a key for the children.
        """
        self.css_grid_template_areas = ''.join("'%s'" % (' '.join(x)) for x in matrix)

    def append(self, value, key=''):
        """Adds a child widget, generating and returning a key if not provided

        In order to access to the specific child in this way container.children[key].

        Args:
            value (Widget, or iterable of Widgets): The child to be appended. In case of a dictionary,
                each item's key is used as 'key' param for the single append.
            key (str): The unique string identifier for the child. Ignored in case of iterable 'value'
                param. The key have to correspond to a an element provided in the 'define_grid' method param.

        Returns:
            str: a key used to refer to the child for all future interaction, or a list of keys in case
                of an iterable 'value' param
        """
        if type(value) in (list, tuple, dict):
            if type(value) == dict:
                for k in value.keys():
                    self.append(value[k], k)
                return value.keys()
            keys = []
            for child in value:
                keys.append(self.append(child))
            return keys

        if not isinstance(value, Widget):
            raise ValueError('value should be a Widget (otherwise use add_child(key,other)')

        key = value.identifier if key == '' else key
        self.add_child(key, value)
        value.style['grid-area'] = key
        value.css_position = 'static'

        return key

    def remove_child(self, child):
        if 'grid-area' in child.style.keys():
            del child.style['grid-area']
        super(GridBox, self).remove_child(child)

    def set_column_sizes(self, values):
        """Sets the size value for each column

        Args:
            values (iterable of int or str): values are treated as percentage.
        """
        self.css_grid_template_columns = ' '.join(map(lambda value: (str(value) if str(value).endswith('%') else str(value) + '%') , values))

    def set_row_sizes(self, values):
        """Sets the size value for each row

        Args:
            values (iterable of int or str): values are treated as percentage.
        """
        self.css_grid_template_rows = ' '.join(map(lambda value: (str(value) if str(value).endswith('%') else str(value) + '%') , values))
    
    def set_column_gap(self, value):
        """Sets the gap value between columns

        Args:
            value (int or str): gap value (i.e. 10 or "10px")
        """
        if self.css_width == "auto":
            if (type(value) == int and value != 0) or value[0] != "0":
                raise CssStyleError("Do not set column gap in combination with width auto")
        if type(value) == int:
            value = str(value) + 'px'
        self.style['grid-column-gap'] = value

    def set_row_gap(self, value):
        """Sets the gap value between rows

        Args:
            value (int or str): gap value (i.e. 10 or "10px")
        """
        if self.css_height == "auto":
            if (type(value) == int and value != 0) or value[0] != "0":
                raise CssStyleError("Do not set row gap in combination with height auto")
        if type(value) == int:
            value = str(value) + 'px'
        self.style['grid-row-gap'] = value

    def set_from_asciiart(self, asciipattern, column_gap=0, row_gap=0):
        """Defines the GridBox layout from a simple and intuitive ascii art table

            Pipe "|" is the column separator.
            Rows are separated by \n .
            Identical rows are considered as unique bigger rows.
            Single table cells must contain the 'key' string value of the contained widgets.
            Column sizes are proportionally applied to the defined grid.
            Columns must be alligned between rows.
            The gap values eventually defined by set_column_gap and set_row_gap are overwritten.

            es.
                \"\"\"
                | |label|button    |
                | |text |          |
                \"\"\"

            Args:
                asciipattern (str): The ascii defined grid
                column_gap (int): Percentage value of the total width to be used as gap between columns
                row_gap (int): Percentage value of the total height to be used as gap between rows

        """
        rows = asciipattern.split("\n")
        # remove empty rows
        for r in rows[:]:
            if len(r.replace(" ", "")) < 1:
                rows.remove(r)
        for ri in range(0, len(rows)):
            # slicing row removing the first and the last separators
            rows[ri] = rows[ri][rows[ri].find("|")+1:rows[ri].rfind("|")]

        columns = collections.OrderedDict()
        row_count = 0
        row_defs = collections.OrderedDict()
        row_max_width = 0

        row_sizes = []
        for ri in range(0, len(rows)):
            if ri > 0:
                if rows[ri] == rows[ri-1]:
                    row_sizes[row_count-1] = row_sizes[row_count-1] + 1 # increment identical row count
                    continue

            row_defs[row_count] = rows[ri].replace(" ","").split("|")
            row_sizes.append(1)
            # placeholder . where cell is empty
            row_defs[row_count] = ['.' if (elem == '') else elem for elem in row_defs[row_count]]
            row_count = row_count + 1
            row_max_width = max(row_max_width, len(rows[ri]))

            i = rows[ri].find("|",0)
            while i > -1:
                columns[i] = i
                i = rows[ri].find("|", i+1)

        columns[row_max_width] = row_max_width

        for r in range(0, len(row_sizes)):
            row_sizes[r] = float(row_sizes[r])/float(len(rows))*(100.0-row_gap*(len(row_sizes)-1))

        column_sizes = []
        prev_size = 0.0
        for c in columns.values():
            value = float(c)/float(row_max_width)*(100.0-column_gap*(len(columns)-1))
            column_sizes.append(value-prev_size)
            prev_size = value

        self.define_grid(row_defs.values())
        self.set_column_sizes(column_sizes)
        self.set_row_sizes(row_sizes)
        self.set_column_gap("%s%%"%column_gap)
        self.set_row_gap("%s%%"%row_gap)


class HBox(Container):
    """The purpose of this widget is to automatically horizontally aligning
        the widgets that are appended to it.
    Does not permit children absolute positioning.

    In order to add children to this container, use the append(child, key) function.
    The key have to be numeric and determines the children order in the layout.

    Note: If you would absolute positioning, use the Container instead.
    """

    def __init__(self, *args, **kwargs):
        super(HBox, self).__init__(*args, **kwargs)

        # fixme: support old browsers
        # http://stackoverflow.com/a/19031640
        self.style.update({'display':'flex', 'flex-direction':'row'})
        self.style['justify-content'] = self.style.get('justify-content', 'space-around')
        self.style['align-items'] = self.style.get('align-items', 'center')

    def append(self, value, key=''):
        """It allows to add child widgets to this.
        The key allows to access the specific child in this way container.children[key].
        The key have to be numeric and determines the children order in the layout.

        Args:
            value (Widget): Child instance to be appended.
            key (str): Unique identifier for the child. If key.isdigit()==True '0' '1'.. the value determines the order
            in the layout
        """
        if type(value) in (list, tuple, dict):
            if type(value)==dict:
                for k in value.keys():
                    self.append(value[k], k)
                return value.keys()
            keys = []
            for child in value:
                keys.append( self.append(child) )
            return keys

        key = str(key)
        if not isinstance(value, Widget):
            raise ValueError('value should be a Widget (otherwise use add_child(key,other)')

        del value.css_left
        del value.css_right

        if value.css_order == None:
            value.css_position = 'static'
            value.css_order = '-1'

        if key.isdigit():
            value.css_order = key

        key = value.identifier if key == '' else key
        self.add_child(key, value)

        return key


class VBox(HBox):
    """The purpose of this widget is to automatically vertically aligning
        the widgets that are appended to it.
    Does not permit children absolute positioning.

    In order to add children to this container, use the append(child, key) function.
    The key have to be numeric and determines the children order in the layout.

    Note: If you would absolute positioning, use the Container instead.
    """

    def __init__(self, *args, **kwargs):
        super(VBox, self).__init__(*args, **kwargs)
        self.css_flex_direction = 'column'


class AsciiContainer(Container):
    widget_layout_map = None

    def __init__(self, *args, **kwargs):
        Container.__init__(self, *args, **kwargs)
        self.css_position = 'relative'
        
    def set_from_asciiart(self, asciipattern, gap_horizontal=0, gap_vertical=0):
        """
            asciipattern (str): a multiline string representing the layout
                | widget1               |
                | widget1               |
                | widget2 | widget3     |
            gap_horizontal (int): a percent value
            gap_vertical (int): a percent value
        """
        pattern_rows = asciipattern.split('\n')
        # remove empty rows
        for r in pattern_rows[:]:
            if len(r.replace(" ", "")) < 1:
                pattern_rows.remove(r)

        layout_height_in_chars = len(pattern_rows)
        self.widget_layout_map = {}
        row_index = 0
        for row in pattern_rows:
            row = row.strip()
            row_width = len(row) - row.count('|') #the row width is calculated without pipes
            row = row[1:-1] #removing |pipes at beginning and end
            columns = row.split('|')

            left_value = 0
            for column in columns:
                widget_key = column.strip()
                widget_width = float(len(column))
                
                if not widget_key in list(self.widget_layout_map.keys()):
                    #width is calculated in percent
                    # height is instead initialized at 1 and incremented by 1 each row the key is present
                    # at the end of algorithm the height will be converted in percent
                    self.widget_layout_map[widget_key] = { 'width': "%.2f%%"%float(widget_width / (row_width) * 100.0 - gap_horizontal), 
                                            'height':1, 
                                            'top':"%.2f%%"%float(row_index / (layout_height_in_chars) * 100.0 + (gap_vertical/2.0)), 
                                            'left':"%.2f%%"%float(left_value / (row_width) * 100.0 + (gap_horizontal/2.0))}
                else:
                    self.widget_layout_map[widget_key]['height'] += 1
                
                left_value += widget_width
            row_index += 1

        #converting height values in percent string
        for key in self.widget_layout_map.keys():
            self.widget_layout_map[key]['height'] = "%.2f%%"%float(self.widget_layout_map[key]['height'] / (layout_height_in_chars) * 100.0 - gap_vertical) 

        for key in self.widget_layout_map.keys():
            self.set_widget_layout(key)

    def append(self, widget, key=''):
        key = Container.append(self, widget, key)
        self.set_widget_layout(key)
        return key

    def set_widget_layout(self, widget_key):
        if not ((widget_key in list(self.children.keys()) and (widget_key in list(self.widget_layout_map.keys())))):
            return
        self.children[widget_key].css_position = 'absolute'
        self.children[widget_key].set_size(self.widget_layout_map[widget_key]['width'], self.widget_layout_map[widget_key]['height'])
        self.children[widget_key].css_left = self.widget_layout_map[widget_key]['left']
        self.children[widget_key].css_top = self.widget_layout_map[widget_key]['top']


class TabBox(Container):
    """ A multipage container.
        Add a tab by doing an append. ie. tabbox.append( widget, "Tab Name" )
        The widget can be a container with other child widgets.
    """
    def __init__(self, *args, **kwargs):
        super(TabBox, self).__init__(layout_orientation=Container.LAYOUT_VERTICAL, *args, **kwargs)
        self.container_tab_titles = ListView( width="100%", layout_orientation=Container.LAYOUT_HORIZONTAL, _class = 'tabs clearfix' )
        self.container_tab_titles.onselection.do(self.on_tab_selection)
        super(TabBox, self).append(self.container_tab_titles, "_container_tab_titles")
        self.selected_widget_key = None
        self.tab_keys_ordered_list = []

    def resize_tab_titles(self):
        nch=len(self.container_tab_titles.children.values())
        # the rounding errors of "%.1f" add upt to more than 100.0%  (e.g. at 7 tabs) , so be more precise here
        tab_w = 0
        if nch > 0:
            if 1000.0 // nch > 0:
                tab_w = 1000.0 // nch  /10
        l = None
        for l in self.container_tab_titles.children.values():
            l.set_size("%.1f%%" % tab_w, "auto")
        # and make last tab consume the rounding rest, looks better
        if not l is None:
            last_tab_w=100.0-tab_w*(nch-1)
            l.set_size("%.1f%%" % last_tab_w, "auto")
        

    def append(self, widget, key=''):
        """ Adds a new tab.
            The *widget* is the content of the tab.
            The *key* is the tab title.
        """
        key = super(TabBox, self).append(widget, key)
        self.tab_keys_ordered_list.append(key)
        self.container_tab_titles.append(ListItem(key), key)
        self.resize_tab_titles()
        #if first tab, select
        if self.selected_widget_key is None:
            self.on_tab_selection(None, key)
        else:
            self.on_tab_selection(None, self.selected_widget_key)

    def remove_child(self, widget):
        key = None
        for k in self.children.keys():
            if hasattr(self.children[k], "identifier"):
                if self.children[k].identifier == widget.identifier:
                    key = k
                    break
        if key:
            self.tab_keys_ordered_list.remove(key)
            self.container_tab_titles.remove_child(self.container_tab_titles.children[key])
        self.resize_tab_titles()
        super(TabBox, self).remove_child(widget)

    @decorate_set_on_listener("(self, emitter, key)")
    @decorate_event
    def on_tab_selection(self, emitter, key):
        #print(str(key))
        for k in self.children.keys():
            w = self.children[k]
            if w is self.container_tab_titles:
                continue
            w.css_display = 'none'
            self.container_tab_titles.children[k].remove_class('active')
            if k==key:
                self.selected_widget_key = k
        self.children[self.selected_widget_key].css_display = 'block'
        self.container_tab_titles.children[self.selected_widget_key].add_class('active')
        return (self.selected_widget_key,)

    def select_by_widget(self, widget):
        for k in self.children.keys():
            if self.children[k] is widget:
                self.on_tab_selection(None, k)
                return

    def select_by_key(self, key):
        self.on_tab_selection(None, key)

    def select_by_name(self, key):
        """ This function is deprecated. Is here for compatibility reasons.
            Use *select_by_key* instead.
        """
        self.select_by_key(key)

    def add_tab(self, widget, key, callback=None):
        """ This function is deprecated. Is here for compatibility reasons.
            The callback is ignored.
            Use *append* instead.
        """
        self.append(widget, key)

    def select_by_index(self, index):
        self.on_tab_selection(None, self.tab_keys_ordered_list[index])


# noinspection PyUnresolvedReferences
class _MixinTextualWidget(object):
    @property
    @editor_attribute_decorator("WidgetSpecific",'''Text content''', str, {})
    def text(self): return self.get_text()
    @text.setter
    def text(self, value): self.set_text(value)

    @property
    @editor_attribute_decorator("Font",'''Specifies whether the text have to be horizontal or vertical.''', 'DropDown', {'possible_values': ('none', 'horizontal-tb', 'vertical-rl', 'vertical-lr')})
    def css_writing_mode(self): return self.style.get('writing-mode', None)
    @css_writing_mode.setter
    def css_writing_mode(self, value): self.style['writing-mode'] = str(value)
    @css_writing_mode.deleter
    def css_writing_mode(self): del self.style['writing-mode']

    @property
    @editor_attribute_decorator("Font",'''Text alignment.''', 'DropDown', {'possible_values': ('none', 'center', 'left', 'right', 'justify')})
    def css_text_align(self): return self.style.get('text-align', None)
    @css_text_align.setter
    def css_text_align(self, value): self.style['text-align'] = str(value)
    @css_text_align.deleter
    def css_text_align(self): del self.style['text-align']

    @property
    @editor_attribute_decorator("Font",'''Text direction.''', 'DropDown', {'possible_values': ('none', 'ltr', 'rtl')})
    def css_direction(self): return self.style.get('direction', None)
    @css_direction.setter
    def css_direction(self, value): self.style['direction'] = str(value)
    @css_direction.deleter
    def css_direction(self): del self.style['direction']

    def set_text(self, text):
        """
        Sets the text label for the Widget.

        Args:
            text (str): The string label of the Widget.
        """
        self.add_child('text', escape(text, quote=False))

    def get_text(self):
        """
        Returns:
            str: The text content of the Widget. You can set the text content with set_text(text).
        """
        if 'text' not in self.children.keys():
            return ''
        return unescape(self.get_child('text'))


class Button(Widget, _MixinTextualWidget):
    """The Button widget. Have to be used in conjunction with its event onclick.
        Use Widget.onclick.connect in order to register the listener.
    """
    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str): The text that will be displayed on the button.
            kwargs: See Widget.__init__()
        """
        super(Button, self).__init__(*args, **kwargs)
        self.type = 'button'
        self.set_text(text)


class TextInput(Widget, _MixinTextualWidget):
    """Editable multiline/single_line text area widget. You can set the content by means of the function set_text or
     retrieve its content with get_text.
    """

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Defines the maximum text content length.''', int, {'possible_values': '', 'min': 0, 'max': 10000, 'default': 0, 'step': 1})
    def attr_maxlength(self): return self.attributes.get('maxlength', '0')
    @attr_maxlength.setter
    def attr_maxlength(self, value): self.attributes['maxlength'] = str(value)
    @attr_maxlength.deleter
    def attr_maxlength(self): del self.attributes['maxlength']

    def __init__(self, single_line=True, hint='', *args, **kwargs):
        """
        Args:
            single_line (bool): Determines if the TextInput have to be single_line. A multiline TextInput have a gripper
                                that allows the resize.
            hint (str): Sets a hint using the html placeholder attribute.
            kwargs: See Widget.__init__()
        """
        super(TextInput, self).__init__(*args, **kwargs)
        self.type = 'textarea'

        self.single_line = single_line
        if single_line:
            self.style['resize'] = 'none'
            self.attributes['rows'] = '1'
            self.attributes[self.EVENT_ONINPUT] = """
                var elem = document.getElementById('%(emitter_identifier)s');
                var enter_pressed = (elem.value.indexOf('\\n') > -1);
                if(enter_pressed){
                    elem.value = elem.value.split('\\n').join('');
                    var params={};params['new_value']=elem.value;
                    remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);
                }""" % {'emitter_identifier': str(self.identifier), 'event_name': Widget.EVENT_ONCHANGE}
        #else:
        #    self.attributes[self.EVENT_ONINPUT] = """
        #        var elem = document.getElementById('%(emitter_identifier)s');
        #        var params={};params['new_value']=elem.value;
        #        remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);
        #        """ % {'emitter_identifier': str(self.identifier), 'event_name': Widget.EVENT_ONCHANGE}

        self.set_value('')

        if hint:
            self.attributes['placeholder'] = hint

        self.attributes['autocomplete'] = 'off'

        self.attributes[Widget.EVENT_ONCHANGE] = \
            "var params={};params['new_value']=document.getElementById('%(emitter_identifier)s').value;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);"% \
            {'emitter_identifier': str(self.identifier), 'event_name': Widget.EVENT_ONCHANGE}

    def set_value(self, text):
        """Sets the text content.

        Args:
            text (str): The string content that have to be appended as standard child identified by the key 'text'
        """
        if self.single_line:
            text = text.replace('\n', '')
        self.set_text(text)

    def get_value(self):
        """
        Returns:
            str: The text content of the TextInput. You can set the text content with set_text(text).
        """
        return self.get_text()

    @decorate_set_on_listener("(self, emitter, new_value)")
    @decorate_event
    def onchange(self, new_value):
        """Called when the user changes the TextInput content.
        With single_line=True it fires in case of focus lost and Enter key pressed.
        With single_line=False it fires at each key released.

        Args:
            new_value (str): the new string content of the TextInput.
        """
        self.disable_update()
        self.set_value(new_value)
        self.enable_update()
        return (new_value, )

    @decorate_set_on_listener("(self, emitter, new_value, keycode)")
    @decorate_event_js("""var elem=document.getElementById('%(emitter_identifier)s');
            var params={};params['new_value']=elem.value;params['keycode']=(event.which||event.keyCode);
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onkeyup(self, new_value, keycode):
        """Called when user types and releases a key into the TextInput

        Note: This event can't be registered together with Widget.onchange.

        Args:
            new_value (str): the new string content of the TextInput
            keycode (str): the numeric char code
        """
        return (new_value, keycode)

    @decorate_set_on_listener("(self, emitter, new_value, keycode)")
    @decorate_event_js("""var elem=document.getElementById('%(emitter_identifier)s');
            var params={};params['new_value']=elem.value;params['keycode']=(event.which||event.keyCode);
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def onkeydown(self, new_value, keycode):
        """Called when the user types a key into the TextInput.

        Note: This event can't be registered together with Widget.onchange.

        Args:
            new_value (str): the new string content of the TextInput.
            keycode (str): the numeric char code
        """
        return (new_value, keycode)


class Label(Widget, _MixinTextualWidget):
    """ Non editable text label widget. Set its content by means of set_text function, and retrieve its content with the
        function get_text.
    """

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str): The string content that have to be displayed in the Label.
            kwargs: See Container.__init__()
        """
        super(Label, self).__init__(*args, **kwargs)
        self.type = 'p'
        self.set_text(text)


class Progress(Widget):
    """ Progress bar widget.
    """

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Defines the actual value for the progress bar.''', int, {'possible_values': '', 'min': 0, 'max': 10000, 'default': 0, 'step': 1})
    def attr_value(self): return self.attributes.get('value', '0')
    @attr_value.setter
    def attr_value(self, value): self.attributes['value'] = str(value)
    @attr_value.deleter
    def attr_value(self): del self.attributes['value']

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Defines the maximum value for the progress bar.''', int, {'possible_values': '', 'min': 0, 'max': 10000, 'default': 0, 'step': 1})
    def attr_max(self): return self.attributes.get('max', '100')
    @attr_max.setter
    def attr_max(self, value): self.attributes['max'] = str(value)
    @attr_max.deleter
    def attr_max(self): del self.attributes['max']

    def __init__(self, value=0, _max=100, *args, **kwargs):
        """
        Args:
            value (int): The actual progress value.
            max (int): The maximum progress value.
            kwargs: See Widget.__init__()
        """
        super(Progress, self).__init__(*args, **kwargs)
        self.type = 'progress'
        self.set_value(value)
        self.attributes['max'] = str(_max)

    def set_value(self, value):
        """
        Args:
            value (int): The actual progress value.
        """
        self.attributes['value'] = str(value)

    def set_max(self, _max):
        """
        Args:
            _max (int): The maximum progress value.
        """
        self.attributes['max'] = str(_max)


class GenericDialog(Container):
    """ Generic Dialog widget. It can be customized to create personalized dialog windows.
        You can setup the content adding content widgets with the functions add_field or add_field_with_label.
        The user can confirm or dismiss the dialog with the common buttons Cancel/Ok.
        Each field added to the dialog can be retrieved by its key, in order to get back the edited value. Use the function
         get_field(key) to retrieve the field.
        The Ok button emits the 'confirm_dialog' event. Register the listener to it with set_on_confirm_dialog_listener.
        The Cancel button emits the 'cancel_dialog' event. Register the listener to it with set_on_cancel_dialog_listener.
    """

    def __init__(self, title='', message='', *args, **kwargs):
        """
        Args:
            title (str): The title of the dialog.
            message (str): The message description.
            kwargs: See Container.__init__()
        """
        super(GenericDialog, self).__init__(*args, **kwargs)
        self.set_layout_orientation(Container.LAYOUT_VERTICAL)
        self.style.update({'display': 'block', 'overflow': 'auto', 'margin': '0px auto'})

        if len(title) > 0:
            t = Label(title)
            t.add_class('DialogTitle')
            self.append(t, "title")

        if len(message) > 0:
            m = Label(message)
            m.css_margin = '5px'
            self.append(m, "message")

        self.container = Container()
        self.container.style.update({'display': 'block', 'overflow': 'auto', 'margin': '5px'})
        self.container.set_layout_orientation(Container.LAYOUT_VERTICAL)
        self.conf = Button('Ok')
        self.conf.set_size(100, 30)
        self.conf.css_margin = '3px'
        self.cancel = Button('Cancel')
        self.cancel.set_size(100, 30)
        self.cancel.css_margin = '3px'
        hlay = Container(height=35)
        hlay.css_display = 'block'
        hlay.style['overflow'] = 'visible'
        hlay.append(self.conf, "confirm_button")
        hlay.append(self.cancel, "cancel_button")
        self.conf.style['float'] = 'right'
        self.cancel.style['float'] = 'right'

        self.append(self.container, "central_container")
        self.append(hlay, "buttons_container")

        self.conf.onclick.connect(self.confirm_dialog)
        self.cancel.onclick.connect(self.cancel_dialog)

        self.inputs = {}

        self._base_app_instance = None
        self._old_root_widget = None

    def add_field_with_label(self, key, label_description, field):
        """
        Adds a field to the dialog together with a descriptive label and a unique identifier.

        Note: You can access to the fields content calling the function GenericDialog.get_field(key).

        Args:
            key (str): The unique identifier for the field.
            label_description (str): The string content of the description label.
            field (Widget): The instance of the field Widget. It can be for example a TextInput or maybe
            a custom widget.
        """
        self.inputs[key] = field
        label = Label(label_description)
        label.css_margin = '0px 5px'
        label.style['min-width'] = '30%'
        container = HBox()
        container.style.update({'justify-content': 'space-between', 'overflow': 'auto', 'padding': '3px'})
        container.append(label, key='lbl' + key)
        container.append(self.inputs[key], key=key)
        self.container.append(container, key=key)

    def add_field(self, key, field):
        """
        Adds a field to the dialog with a unique identifier.

        Note: You can access to the fields content calling the function GenericDialog.get_field(key).

        Args:
            key (str): The unique identifier for the field.
            field (Widget): The widget to be added to the dialog, TextInput or any Widget for example.
        """
        self.inputs[key] = field
        container = HBox()
        container.style.update({'justify-content': 'space-between', 'overflow': 'auto', 'padding': '3px'})
        container.append(self.inputs[key], key=key)
        self.container.append(container, key=key)

    def get_field(self, key):
        """
        Args:
            key (str): The unique string identifier of the required field.

        Returns:
            Widget field instance added previously with methods GenericDialog.add_field or
            GenericDialog.add_field_with_label.
        """
        return self.inputs[key]

    @decorate_set_on_listener("(self,emitter)")
    @decorate_event
    def confirm_dialog(self, emitter):
        """Event generated by the OK button click.
        """
        self.hide()
        return ()

    @decorate_set_on_listener("(self,emitter)")
    @decorate_event
    def cancel_dialog(self, emitter):
        """Event generated by the Cancel button click."""
        self.hide()
        return ()

    def show(self, base_app_instance):
        self._base_app_instance = base_app_instance
        self._old_root_widget = self._base_app_instance.root
        self._base_app_instance.set_root_widget(self)

    def hide(self):
        self._base_app_instance.set_root_widget(self._old_root_widget)


class InputDialog(GenericDialog):
    """Input Dialog widget. It can be used to query simple and short textual input to the user.
    The user can confirm or dismiss the dialog with the common buttons Cancel/Ok.
    The Ok button click or the ENTER key pression emits the 'confirm_dialog' event. Register the listener to it
    with set_on_confirm_dialog_listener.
    The Cancel button emits the 'cancel_dialog' event. Register the listener to it with set_on_cancel_dialog_listener.
    """

    def __init__(self, title='Title', message='Message', initial_value='', *args, **kwargs):
        """
        Args:
            title (str): The title of the dialog.
            message (str): The message description.
            initial_value (str): The default content for the TextInput field.
            kwargs: See Container.__init__()
        """
        super(InputDialog, self).__init__(title, message, *args, **kwargs)

        self.inputText = TextInput()
        self.inputText.onkeydown.connect(self.on_keydown_listener)
        self.add_field('textinput', self.inputText)
        self.inputText.set_text(initial_value)

        self.confirm_dialog.connect(self.confirm_value)

    def on_keydown_listener(self, widget, value, keycode):
        """event called pressing on ENTER key.

        propagates the string content of the input field
        """
        if keycode == "13":
            self.hide()
            self.inputText.set_text(value)
            self.confirm_value(self)

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def confirm_value(self, widget):
        """Event called pressing on OK button."""
        return (self.inputText.get_text(),)


class ListView(Container):
    """List widget it can contain ListItems. Add items to it by using the standard append(item, key) function or
    generate a filled list from a string list by means of the function new_from_list. Use the list in conjunction of
    its onselection event. Register a listener with ListView.onselection.connect.
    """

    def __init__(self, selectable=True, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(ListView, self).__init__(*args, **kwargs)
        self.type = 'ul'
        self._selected_item = None
        self._selected_key = None
        self._selectable = selectable

    @classmethod
    def new_from_list(cls, items, **kwargs):
        """Populates the ListView with a string list.

        Args:
            items (list): list of strings to fill the widget with.
        """
        obj = cls(**kwargs)
        for item in items:
            obj.append(ListItem(item))
        return obj

    def append(self, value, key=''):
        """Appends child items to the ListView. The items are accessible by list.children[key].

        Args:
            value (ListItem, or iterable of ListItems): The child to be appended. In case of a dictionary,
                each item's key is used as 'key' param for the single append.
            key (str): The unique string identifier for the child. Ignored in case of iterable 'value'
                param.
        """
        if isinstance(value, type('')) or isinstance(value, type(u'')):
            value = ListItem(value)

        keys = super(ListView, self).append(value, key=key)
        if type(value) in (list, tuple, dict):
            for k in keys:
                if self.EVENT_ONCLICK not in self.children[k].attributes:
                    self.children[k].onclick.connect(self.onselection)
                self.children[k].attributes['selected'] = False
        else:
            # if an event listener is already set for the added item, it will not generate a selection event
            if self.EVENT_ONCLICK not in value.attributes:
                value.onclick.connect(self.onselection)
            value.attributes['selected'] = False
        return keys

    def empty(self):
        """Removes all children from the list"""
        self._selected_item = None
        self._selected_key = None
        super(ListView, self).empty()

    @decorate_set_on_listener("(self,emitter,selectedKey)")
    @decorate_event
    def onselection(self, widget):
        """Called when a new item gets selected in the list."""
        self._selected_key = None
        for k in self.children:
            if self.children[k] == widget:  # widget is the selected ListItem
                self._selected_key = k
                if (self._selected_item is not None) and self._selectable:
                    self._selected_item.attributes['selected'] = False
                self._selected_item = self.children[self._selected_key]
                if self._selectable:
                    self._selected_item.attributes['selected'] = True
                break
        return (self._selected_key,)

    def get_item(self):
        """
        Returns:
            ListItem: The selected item or None
        """
        return self._selected_item

    def get_value(self):
        """
        Returns:
            str: The value of the selected item or None
        """
        if self._selected_item is None:
            return None
        return self._selected_item.get_value()

    def get_key(self):
        """
        Returns:
            str: The key of the selected item or None if no item is selected.
        """
        return self._selected_key

    def select_by_key(self, key):
        """Selects an item by its key.

        Args:
            key (str): The unique string identifier of the item that have to be selected.
        """
        self._selected_key = None
        self._selected_item = None
        for item in self.children.values():
            item.attributes['selected'] = False

        if key in self.children:
            self.children[key].attributes['selected'] = True
            self._selected_key = key
            self._selected_item = self.children[key]

    def set_value(self, value):
        self.select_by_value(value)

    def select_by_value(self, value):
        """Selects an item by the text content of the child.

        Args:
            value (str): Text content of the item that have to be selected.
        """
        self._selected_key = None
        self._selected_item = None
        for k in self.children:
            item = self.children[k]
            item.attributes['selected'] = False
            if value == item.get_value():
                self._selected_key = k
                self._selected_item = item
                self._selected_item.attributes['selected'] = True


class ListItem(Widget, _MixinTextualWidget):
    """List item widget for the ListView.

    ListItems are characterized by a textual content. They can be selected from
    the ListView. Do NOT manage directly its selection by registering set_on_click_listener, use instead the events of
    the ListView.
    """

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str, unicode): The textual content of the ListItem.
            kwargs: See Widget.__init__()
        """
        super(ListItem, self).__init__(*args, **kwargs)
        self.type = 'li'
        self.set_text(text)

    def get_value(self):
        """
        Returns:
            str: The text content of the ListItem
        """
        return self.get_text()


class DropDown(Container):
    """Drop down selection widget. Implements the onchange(value) event. Register a listener for its selection change
    by means of the function DropDown.onchange.connect.
    """

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(DropDown, self).__init__(*args, **kwargs)
        self.type = 'select'
        self.attributes[self.EVENT_ONCHANGE] = \
            "var params={};params['value']=document.getElementById('%(id)s').value;" \
            "remi.sendCallbackParam('%(id)s','%(evt)s',params);" % {'id': self.identifier,
                                                               'evt': self.EVENT_ONCHANGE}
        self._selected_item = None
        self._selected_key = None

    @classmethod
    def new_from_list(cls, items, **kwargs):
        item = None
        obj = cls(**kwargs)
        for item in items:
            obj.append(DropDownItem(item))
        if item is not None:
            try:
                obj.select_by_value(item)  # ensure one is selected
            except UnboundLocalError:
                pass
        return obj

    def append(self, value, key=''):
        if isinstance(value, type('')) or isinstance(value, type(u'')):
            value = DropDownItem(value)
        keys = super(DropDown, self).append(value, key=key)
        if len(self.children) == 1:
            self.select_by_value(value.value)
        return keys

    def empty(self):
        self._selected_item = None
        self._selected_key = None
        super(DropDown, self).empty()

    def select_by_key(self, key):
        """Selects an item by its unique string identifier.

        Args:
            key (str): Unique string identifier of the DropDownItem that have to be selected.
        """
        for item in self.children.values():
            if 'selected' in item.attributes:
                del item.attributes['selected']
        self.children[key].attributes['selected'] = 'selected'
        self._selected_key = key
        self._selected_item = self.children[key]

    def set_value(self, value):
        self.select_by_value(value)

    def select_by_value(self, value):
        """Selects a DropDownItem by means of the contained text-

        Args:
            value (str): Textual content of the DropDownItem that have to be selected.
        """
        self._selected_key = None
        self._selected_item = None
        for k in self.children:
            item = self.children[k]
            if item.value == value:
                item.attributes['selected'] = 'selected'
                self._selected_key = k
                self._selected_item = item
                log.debug('dropdown selected item with value %s' % value)
            else:
                if 'selected' in item.attributes:
                    del item.attributes['selected']

    def get_item(self):
        """
        Returns:
            DropDownItem: The selected item or None.
        """
        return self._selected_item

    def get_value(self):
        """
        Returns:
            str: The value of the selected item or None.
        """
        if self._selected_item is None:
            return None
        return self._selected_item.value

    def get_key(self):
        """
        Returns:
            str: The unique string identifier of the selected item or None.
        """
        return self._selected_key

    @decorate_set_on_listener("(self,emitter,new_value)")
    @decorate_event
    def onchange(self, value):
        """Called when a new DropDownItem gets selected.
        """
        self.disable_update()
        self.select_by_value(value)
        self.enable_update()
        return (value, )


class DropDownItem(Widget, _MixinTextualWidget):
    """item widget for the DropDown"""
    @property
    @editor_attribute_decorator("WidgetSpecific", '''The value returned to the DropDown in onchange event. 
        By default it corresponds to the displayed text, unsless it is changes.''', str, {})
    def value(self): return unescape(self.attributes.get('value', '').replace('&nbsp;', ' '))
    @value.setter
    def value(self, value): self.attributes['value'] = escape(value.replace('&nbsp;', ' '), quote=False)

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            kwargs: See Widget.__init__()
        """
        super(DropDownItem, self).__init__(*args, **kwargs)
        self.type = 'option'
        self.value = text
        self.set_text(text)

    def set_text(self, text):
        """
        Sets the text label for the Widget.

        Args:
            text (str): The string label of the Widget.
        """
        _MixinTextualWidget.set_text(self, text)
        self.children['text'] = self.children['text'].replace(' ', '&nbsp;')

    def get_text(self):
        """
        Returns:
            str: The text content of the Widget. You can set the text content with set_text(text).
        """
        if 'text' not in self.children.keys():
            return ''
        return unescape(self.get_child('text').replace('&nbsp;', ' '))


class Image(Widget):
    """image widget."""
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Image data or url''', 'base64_image', {})
    def attr_src(self): return self.attributes.get('src', '')
    @attr_src.setter
    def attr_src(self, value): self.attributes['src'] = str(value)
    @attr_src.deleter
    def attr_src(self): del self.attributes['src']

    def __init__(self, image='', *args, **kwargs):
        """
        Args:
            image (str): an url to an image or a base64 data string
            kwargs: See Widget.__init__()
        """
        super(Image, self).__init__(*args, **kwargs)
        self.type = 'img'
        self.attributes['src'] = image

    def set_image(self, image):
        """
        Args:
            image (str): an url to an image or a base64 data string
        """
        self.attributes['src'] = image


class Table(Container):
    """
    table widget - it will contains TableRow
    """

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(Table, self).__init__(*args, **kwargs)

        self.type = 'table'
        self.style['float'] = 'none'

    @classmethod
    def new_from_list(cls, content, fill_title=True, **kwargs):
        """Populates the Table with a list of tuples of strings.

        Args:
            content (list): list of tuples of strings. Each tuple is a row.
            fill_title (bool): if true, the first tuple in the list will
                be set as title
        """
        obj = cls(**kwargs)
        obj.append_from_list(content, fill_title)
        return obj

    def append_from_list(self, content, fill_title=False):
        """
        Appends rows created from the data contained in the provided
        list of tuples of strings. The first tuple of the list can be
        set as table title.

        Args:
            content (list): list of tuples of strings. Each tuple is a row.
            fill_title (bool): if true, the first tuple in the list will
                be set as title.
        """
        row_index = 0
        for row in content:
            tr = TableRow()
            column_index = 0
            for item in row:
                if row_index == 0 and fill_title:
                    ti = TableTitle(item)
                else:
                    ti = TableItem(item)
                tr.append(ti, str(column_index))
                column_index = column_index + 1
            self.append(tr, str(row_index))
            row_index = row_index + 1

    def append(self, value, key=''):
        keys = super(Table, self).append(value, key)
        if type(value) in (list, tuple, dict):
            for k in keys:
                self.children[k].on_row_item_click.connect(self.on_table_row_click)
        else:
            value.on_row_item_click.connect(self.on_table_row_click)
        return keys

    @decorate_set_on_listener("(self, emitter, row, item)")
    @decorate_event
    def on_table_row_click(self, row, item):
        return (row, item)


class TableWidget(Table):
    """
    Basic table model widget.
    Each item is addressed by stringified integer key in the children dictionary.
    """
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Table colum count.''', int, {'possible_values': '', 'min': 0, 'max': 100, 'default': 1, 'step': 1})
    def column_count(self): return self.__column_count
    @column_count.setter
    def column_count(self, value): self.set_column_count(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Table row count.''', int, {'possible_values': '', 'min': 0, 'max': 100, 'default': 1, 'step': 1})
    def row_count(self): return len(self.children)
    @row_count.setter
    def row_count(self, value): self.set_row_count(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Table use title.''', bool, {})
    def use_title(self): return self.__use_title
    @use_title.setter
    def use_title(self, value): self.set_use_title(value)

    def __init__(self, n_rows=2, n_columns=2, use_title=True, editable=False, *args, **kwargs):
        """
        Args:
            use_title (bool): enable title bar. Note that the title bar is
                treated as a row (it is comprised in n_rows count)
            n_rows (int): number of rows to create
            n_columns (int): number of columns to create
            kwargs: See Container.__init__()
        """
        self.__column_count = 0
        self.__use_title = use_title 
        super(TableWidget, self).__init__(*args, **kwargs)
        self._editable = editable
        self.set_use_title(use_title)
        self.set_column_count(n_columns)
        self.set_row_count(n_rows)
        self.css_display = 'table'

    def set_use_title(self, use_title):
        """Returns the TableItem instance at row, column cordinates

        Args:
            use_title (bool): enable title bar.
        """
        self.__use_title = use_title
        self._update_first_row()

    def _update_first_row(self):
        cl = TableEditableItem if self._editable else TableItem
        if self.__use_title:
            cl = TableTitle

        if len(self.children) > 0:
            for c_key in self.children['0'].children.keys():
                instance = cl(self.children['0'].children[c_key].get_text())
                self.children['0'].append(instance, c_key)
                # here the cells of the first row are overwritten and aren't appended by the standard Table.append
                # method. We have to restore de standard on_click internal listener in order to make it working
                # the Table.on_table_row_click functionality
                instance.onclick.connect(self.children['0'].on_row_item_click)

    def item_at(self, row, column):
        """Returns the TableItem instance at row, column cordinates

        Args:
            row (int): zero based index
            column (int): zero based index
        """
        return self.children[str(row)].children[str(column)]

    def item_coords(self, table_item):
        """Returns table_item's (row, column) cordinates.
        Returns None in case of item not found.

        Args:
            table_item (TableItem): an item instance
        """
        for row_key in self.children.keys():
            for item_key in self.children[row_key].children.keys():
                if self.children[row_key].children[item_key] == table_item:
                    return (int(row_key), int(item_key))
        return None

    def set_row_count(self, count):
        """Sets the table row count.

        Args:
            count (int): number of rows
        """
        current_row_count = self.row_count
        current_column_count = self.column_count
        if count > current_row_count:
            cl = TableEditableItem if self._editable else TableItem
            for i in range(current_row_count, count):
                tr = TableRow()
                for c in range(0, current_column_count):
                    tr.append(cl(), str(c))
                    if self._editable:
                        tr.children[str(c)].onchange.connect(
                            self.on_item_changed, int(i), int(c))
                self.append(tr, str(i))
            self._update_first_row()
        elif count < current_row_count:
            for i in range(count, current_row_count):
                self.remove_child(self.children[str(i)])

    def set_column_count(self, count):
        """Sets the table column count.

        Args:
            count (int): column of rows
        """
        current_row_count = self.row_count
        current_column_count = self.column_count
        if count > current_column_count:
            cl = TableEditableItem if self._editable else TableItem
            for r_key in self.children.keys():
                row = self.children[r_key]
                for i in range(current_column_count, count):
                    row.append(cl(), str(i))
                    if self._editable:
                        row.children[str(i)].onchange.connect(
                            self.on_item_changed, int(r_key), int(i))
            self._update_first_row()
        elif count < current_column_count:
            for row in self.children.values():
                for i in range(count, current_column_count):
                    row.remove_child(row.children[str(i)])
        self.__column_count = count

    @decorate_set_on_listener("(self, emitter, item, new_value, row, column)")
    @decorate_event
    def on_item_changed(self, item, new_value, row, column):
        """Event for the item change.

        Args:
            emitter (TableWidget): The emitter of the event.
            item (TableItem): The TableItem instance.
            new_value (str): New text content.
            row (int): row index.
            column (int): column index.
        """
        return (item, new_value, row, column)


class TableRow(Container):
    """
    row widget for the Table - it will contains TableItem
    """

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(TableRow, self).__init__(*args, **kwargs)
        self.type = 'tr'
        self.style['float'] = 'none'

    def append(self, value, key=''):
        if isinstance(value, type('')) or isinstance(value, type(u'')):
            value = TableItem(value)
        keys = super(TableRow, self).append(value, key)
        if type(value) in (list, tuple, dict):
            for k in keys:
                self.children[k].onclick.connect(self.on_row_item_click)
        else:
            value.onclick.connect(self.on_row_item_click)
        return keys

    @decorate_set_on_listener("(self, emitter, item)")
    @decorate_event
    def on_row_item_click(self, item):
        """Event on item click.

        Note: This is internally used by the Table widget in order to generate the
            Table.on_table_row_click event.
            Use Table.on_table_row_click instead.
        Args:
            emitter (TableRow): The emitter of the event.
            item (TableItem): The clicked TableItem.
        """
        return (item, )


class TableEditableItem(Container, _MixinTextualWidget):
    """item widget for the TableRow."""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str):
            kwargs: See Container.__init__()
        """
        super(TableEditableItem, self).__init__(*args, **kwargs)
        self.type = 'td'
        self.editInput = TextInput()
        self.append(self.editInput)
        self.editInput.onchange.connect(self.onchange)
        self.get_text = self.editInput.get_text
        self.set_text = self.editInput.set_text
        self.set_text(text)

    @decorate_set_on_listener("(self, emitter, new_value)")
    @decorate_event
    def onchange(self, emitter, new_value):
        return (new_value, )


class TableItem(Container, _MixinTextualWidget):
    """item widget for the TableRow."""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str):
            kwargs: See Container.__init__()
        """
        super(TableItem, self).__init__(*args, **kwargs)
        self.type = 'td'
        self.set_text(text)


class TableTitle(TableItem, _MixinTextualWidget):
    """title widget for the table."""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str):
            kwargs: See Widget.__init__()
        """
        super(TableTitle, self).__init__(text, *args, **kwargs)
        self.type = 'th'


class Input(Widget):

    def __init__(self, input_type='', default_value='', *args, **kwargs):
        """
        Args:
            input_type (str): HTML5 input type
            default_value (str):
            kwargs: See Widget.__init__()
        """
        if '_class' not in kwargs:
            kwargs['_class'] = input_type
        super(Input, self).__init__(*args, **kwargs)
        self.type = 'input'

        self.attributes['value'] = str(default_value)
        self.attributes['type'] = input_type
        self.attributes['autocomplete'] = 'off'
        self.attributes[Widget.EVENT_ONCHANGE] = \
            "var params={};params['value']=document.getElementById('%(emitter_identifier)s').value;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);"% \
        {'emitter_identifier':str(self.identifier), 'event_name':Widget.EVENT_ONCHANGE}

    def set_value(self, value):
        self.attributes['value'] = str(value)

    def get_value(self):
        """returns the new text value."""
        return self.attributes['value']

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def onchange(self, value):
        self.attributes['value'] = value
        return (value, )

    def set_read_only(self, readonly):
        if readonly:
            self.attributes['readonly'] = None
        else:
            try:
                del self.attributes['readonly']
            except KeyError:
                pass


class CheckBoxLabel(HBox):

    _checkbox = None
    _label = None

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Text content''', str, {})
    def text(self): return self._label.get_text()
    @text.setter
    def text(self, value): self._label.set_text(value)

    def __init__(self, label='checkbox', checked=False, user_data='', **kwargs):
        """
        Args:
            label (str):
            checked (bool):
            user_data (str):
            kwargs: See Widget.__init__()
        """
        super(CheckBoxLabel, self).__init__(**kwargs)
        self._checkbox = CheckBox(checked, user_data)
        self._label = Label(label)
        self.append(self._checkbox, key='checkbox')
        self.append(self._label, key='label')

        self.set_value = self._checkbox.set_value
        self.get_value = self._checkbox.get_value

        self._checkbox.onchange.connect(self.onchange)

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def onchange(self, widget, value):
        return (value, )

    def get_text(self):
        return self._label.get_text()

    def set_text(self, t):
        self._label.set_text(t)


class CheckBox(Input):
    """check box widget useful as numeric input field implements the onchange event."""

    def __init__(self, checked=False, user_data='', **kwargs):
        """
        Args:
            checked (bool):
            user_data (str):
            kwargs: See Widget.__init__()
        """
        super(CheckBox, self).__init__('checkbox', user_data, **kwargs)
        self.set_value(checked)
        self.attributes[Widget.EVENT_ONCHANGE] = \
            "var params={};params['value']=document.getElementById('%(emitter_identifier)s').checked;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);"% \
            {'emitter_identifier':str(self.identifier), 'event_name':Widget.EVENT_ONCHANGE}

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def onchange(self, value):
        value = value in ('True', 'true')
        self.set_value(value)
        return (value, )

    def set_value(self, checked):
        if checked:
            self.attributes['checked'] = 'checked'
        else:
            if 'checked' in self.attributes:
                del self.attributes['checked']

    def get_value(self):
        """
        Returns:
            bool:
        """
        return 'checked' in self.attributes


class SpinBox(Input):
    """spin box widget useful as numeric input field implements the onchange event.
    """
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the actual value for the spin box.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_value(self): return self.attributes.get('value', '0')
    @attr_value.setter
    def attr_value(self, value): self.attributes['value'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the minimum value for the spin box.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_min(self): return self.attributes.get('min', '0')
    @attr_min.setter
    def attr_min(self, value): self.attributes['min'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the maximum value for the spin box.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_max(self): return self.attributes.get('max', '65535')
    @attr_max.setter
    def attr_max(self, value): self.attributes['max'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the step value for the spin box.''', float, {'possible_values': '', 'min': 0.0, 'max': 65535.0, 'default': 0, 'step': 1})
    def attr_step(self): return self.attributes.get('step', '1')
    @attr_step.setter
    def attr_step(self, value): self.attributes['step'] = str(value)

    # noinspection PyShadowingBuiltins
    def __init__(self, default_value=0, min_value=0, max_value=65535, step=1, allow_editing=True, **kwargs):
        """
        Args:
            default_value (int, float, str):
            min (int, float, str):
            max (int, float, str):
            step (int, float, str):
            allow_editing (bool): If true allow editing the value using backpspace/delete/enter (otherwise
            only allow entering numbers)
            kwargs: See Widget.__init__()
        """
        super(SpinBox, self).__init__('number', str(default_value), **kwargs)
        self.attributes['min'] = str(min_value)
        self.attributes['max'] = str(max_value)
        self.attributes['step'] = str(step)
        # eat non-numeric input (return false to stop propagation of event to onchange listener)
        js = 'var key = event.keyCode || event.charCode;'
        js += 'return (event.charCode >= 48 && event.charCode <= 57)'
        if allow_editing:
            js += ' || (key == 8 || key == 46 || key == 45|| key == 44 )'  # allow backspace and delete and minus and coma
            js += ' || (key == 13)'  # allow enter
        self.attributes[self.EVENT_ONKEYPRESS] = '%s;' % js
        # FIXES Edge behaviour where onchange event not fires in case of key arrow Up or Down
        self.attributes[self.EVENT_ONKEYUP] = \
            "var key = event.keyCode || event.charCode;" \
            "if(key==13){var params={};params['value']=document.getElementById('%(id)s').value;" \
            "remi.sendCallbackParam('%(id)s','%(evt)s',params); return true;}" \
            "return false;" % {'id': self.identifier, 'evt': self.EVENT_ONCHANGE}
        
    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def onchange(self, value):
        _type = int
        try:
            _, _, _ = int(value), int(self.attributes['min']), int(self.attributes['max'])
        except:
            _type = float

        try:
            _value = max(_type(value), _type(self.attributes['min']))
            _value = min(_type(_value), _type(self.attributes['max']))

            self.disable_update()
            self.attributes['value'] = str(_value)
            self.enable_update()

            #this is to force update in case a value out of limits arrived
            # and the limiting ended up with the same previous value stored in self.attributes
            # In this case the limitation gets not updated in browser 
            # (because not triggering is_changed). So the update is forced.
            if _type(value) != _value:
                self.attributes.onchange()
        except:
            #if the value conversion fails the client gui is updated with its previous value
            _type = int
            try:
                _, _, _ = int(self.attributes['value']), int(self.attributes['min']), int(self.attributes['max'])
            except:
                _type = float
            _value = _type(self.attributes['value'])
            self.attributes.onchange()

        return (_value, )


class Slider(Input):

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the actual value for the Slider.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_value(self): return self.attributes.get('value', '0')
    @attr_value.setter
    def attr_value(self, value): self.attributes['value'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the minimum value for the Slider.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_min(self): return self.attributes.get('min', '0')
    @attr_min.setter
    def attr_min(self, value): self.attributes['min'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the maximum value for the Slider.''', float, {'possible_values': '', 'min': -65535, 'max': 65535, 'default': 0, 'step': 1})
    def attr_max(self): return self.attributes.get('max', '65535')
    @attr_max.setter
    def attr_max(self, value): self.attributes['max'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the step value for the Slider.''', float, {'possible_values': '', 'min': 0.0, 'max': 65535.0, 'default': 0, 'step': 1})
    def attr_step(self): return self.attributes.get('step', '1')
    @attr_step.setter
    def attr_step(self, value): self.attributes['step'] = str(value)

    # noinspection PyShadowingBuiltins
    def __init__(self, default_value=0, min=0, max=65535, step=1, **kwargs):
        """
        Args:
            default_value (str):
            min (int):
            max (int):
            step (int):
            kwargs: See Widget.__init__()
        """
        super(Slider, self).__init__('range', default_value, **kwargs)
        self.attributes['min'] = str(min)
        self.attributes['max'] = str(max)
        self.attributes['step'] = str(step)
        self.attributes[Widget.EVENT_ONCHANGE] = \
            "var params={};params['value']=document.getElementById('%(emitter_identifier)s').value;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);"% \
            {'emitter_identifier':str(self.identifier), 'event_name':Widget.EVENT_ONCHANGE}

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def oninput(self, value):
        return (value, )


class ColorPicker(Input):

    def __init__(self, default_value='#995500', **kwargs):
        """
        Args:
            default_value (str): hex rgb color string (#rrggbb)
            kwargs: See Widget.__init__()
        """
        super(ColorPicker, self).__init__('color', default_value, **kwargs)


class Date(Input):

    def __init__(self, default_value='2015-04-13', **kwargs):
        """
        Args:
            default_value (str): date string (yyyy-mm-dd)
            kwargs: See Widget.__init__()
        """
        super(Date, self).__init__('date', default_value, **kwargs)


class Datalist(Container):
    def __init__(self, options=None, *args, **kwargs):
        super(Datalist, self).__init__(*args, **kwargs)
        self.type = 'datalist'
        self.css_display = 'none'
        if options:
            self.append(options)

    def append(self, options, key=''):
        if type(options) in (list, tuple, dict):
            if type(options) == dict:
                for k in options.keys():
                    self.append(options[k], k)
                return options.keys()
            keys = []
            for child in options:
                keys.append(self.append(child))
            return keys

        if not isinstance(options, DatalistItem):
            raise ValueError('options should be an Option or an iterable of Option(otherwise use add_child(key,other)')

        key = options.identifier if key == '' else key
        self.add_child(key, options)
        return key


class DatalistItem(Widget, _MixinTextualWidget):
    """item widget for the DropDown"""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            kwargs: See Widget.__init__()
        """
        super(DatalistItem, self).__init__(*args, **kwargs)
        self.type = 'option'
        self.set_text(text)

    def set_value(self, text):
        return self.set_text(text)

    def get_value(self):
        return self.get_text()


class SelectionInput(Input):
    """ selection input widget useful list selection field implements the oninput event.
        https://developer.mozilla.org/en-US/docs/Web/HTML/Element/datalist
    """

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the actual value for the widget.''', str, {})
    def attr_value(self): return self.attributes.get('value', '0')
    @attr_value.setter
    def attr_value(self, value): self.attributes['value'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the datalist.''', str, {})
    def attr_datalist_identifier(self): 
        return self.attributes.get('list', '0')
    @attr_datalist_identifier.setter
    def attr_datalist_identifier(self, value): 
        if isinstance(value, Datalist):
            value = value.identifier
        self.attributes['list'] = value

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the view type.''', 'DropDown', {'possible_values': ('text', 'search', 'url', 'tel', 'email', 'date', 'month', 'week', 'time', 'datetime-local', 'number', 'range', 'color')})
    def attr_input_type(self): return self.attributes.get('type', 'text')
    @attr_input_type.setter
    def attr_input_type(self, value): self.attributes['type'] = str(value)

    def __init__(self, default_value="", input_type="text", *args, **kwargs):
        """
        Args:
            selection_type (str): text, search, url, tel, email, date, month, week, time, datetime-local, number, range, color. 
            kwargs: See Widget.__init__()
        """
        super(SelectionInput, self).__init__(input_type, default_value, *args, **kwargs)
        
        self.attributes[Widget.EVENT_ONCHANGE] = \
            "var params={};params['value']=document.getElementById('%(emitter_identifier)s').value;" \
            "remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);"% \
            {'emitter_identifier':str(self.identifier), 'event_name':Widget.EVENT_ONCHANGE}

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event_js("""var params={};
            params['value']=this.value;
            remi.sendCallbackParam('%(emitter_identifier)s','%(event_name)s',params);""")
    def oninput(self, value):
        self.disable_update()
        self.set_value(value)
        self.enable_update()
        return (value, )

    def set_value(self, value):
        self.attr_value = value

    def get_value(self):
        """
        Returns:
            str: the actual value
        """
        return self.attr_value

    def set_datalist_identifier(self, datalist):
        self.attr_datalist_identifier = datalist

    def get_datalist_identifier(self):
        return self.attr_datalist_identifier


class SelectionInputWidget(Container):
    datalist = None #the internal Datalist 
    selection_input = None #the internal selection_input

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the actual value for the widget.''', str, {})
    def attr_value(self): return self.selection_input.attr_value
    @attr_value.setter
    def attr_value(self, value): self.selection_input.attr_value = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the view type.''', 'DropDown', {'possible_values': ('text', 'search', 'url', 'tel', 'email', 'date', 'month', 'week', 'time', 'datetime-local', 'number', 'range', 'color')})
    def attr_input_type(self): return self.selection_input.attr_input_type
    @attr_input_type.setter
    def attr_input_type(self, value): self.selection_input.attr_input_type = str(value)

    def __init__(self, iterable_of_str=None, default_value="", input_type='text', *args, **kwargs):
        super(SelectionInputWidget, self).__init__(*args, **kwargs)
        options = None
        if iterable_of_str:
            options = list(map(DatalistItem, iterable_of_str))
        self.datalist = Datalist(options)
        self.selection_input = SelectionInput(default_value, input_type, style={'top':'0px', 
                                                'left':'0px', 'bottom':'0px', 'right':'0px'})
        self.selection_input.set_datalist_identifier(self.datalist.identifier)
        self.append([self.datalist, self.selection_input])
        self.selection_input.oninput.do(self.oninput)
    
    def set_value(self, value):
        """ 
        Sets the value of the widget
        Args:
            value (str): the string value
        """
        self.attr_value = value

    def get_value(self):
        """
        Returns:
            str: the actual value
        """
        return self.attr_value

    @decorate_set_on_listener("(self, emitter, value)")
    @decorate_event
    def oninput(self, emitter, value):
        """ 
        This event occurs when user inputs a new value
        Returns:
            value (str): the string value
        """
        return (value, )


class GenericObject(Widget):
    """
    GenericObject widget - allows to show embedded object like pdf,swf..
    """

    def __init__(self, filename, **kwargs):
        """
        Args:
            filename (str): URL
            kwargs: See Widget.__init__()
        """
        super(GenericObject, self).__init__(**kwargs)
        self.type = 'object'
        self.attributes['data'] = filename


class FileFolderNavigator(GridBox):
    """FileFolderNavigator widget."""

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines wether it is possible to select multiple items.''', bool, {})
    def multiple_selection(self): return self._multiple_selection
    @multiple_selection.setter
    def multiple_selection(self, value): self._multiple_selection = value

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the actual navigator location.''', str, {})
    def selection_folder(self): return self._selection_folder
    @selection_folder.setter
    def selection_folder(self, value): 
        # fixme: we should use full paths and not all this chdir stuff
        self.chdir(value)  # move to actual working directory

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines if files are selectable.''', bool, {})
    def allow_file_selection(self): return self._allow_file_selection
    @allow_file_selection.setter
    def allow_file_selection(self, value): self._allow_file_selection = value

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines if folders are selectable.''', bool, {})
    def allow_folder_selection(self): return self._allow_folder_selection
    @allow_folder_selection.setter
    def allow_folder_selection(self, value): self._allow_folder_selection = value

    def __init__(self, multiple_selection = False, selection_folder = ".", allow_file_selection = True, allow_folder_selection = False, **kwargs):
        super(FileFolderNavigator, self).__init__(**kwargs)

        self.css_grid_template_columns = "30px auto 30px"
        self.css_grid_template_rows = "30px auto"
        self.define_grid([('button_back','url_editor','button_go'), ('items','items','items')])

        self.multiple_selection = multiple_selection
        self.allow_file_selection = allow_file_selection
        self.allow_folder_selection = allow_folder_selection
        self.selectionlist = []
        self.currDir = ''
        self.controlBack = Button('Up')
        self.controlBack.onclick.connect(self.dir_go_back)
        self.controlGo = Button('Go >>')
        self.controlGo.onclick.connect(self.dir_go)
        self.pathEditor = TextInput()
        self.pathEditor.style['resize'] = 'none'
        self.pathEditor.attributes['rows'] = '1'
        self.append(self.controlBack, "button_back")
        self.append(self.pathEditor, "url_editor")
        self.append(self.controlGo, "button_go")

        self.itemContainer = Container(width='100%', height='100%')

        self.append(self.itemContainer, key='items')  # defined key as this is replaced later

        self.folderItems = list()

        self.selection_folder = selection_folder

    def get_selection_list(self):
        if self.allow_folder_selection and not self.selectionlist:
            self.selectionlist.append(self.currDir)
        return self.selectionlist

    def populate_folder_items(self, directory):
        def _sort_files(a, b):
            if os.path.isfile(a) and os.path.isdir(b):
                return 1
            elif os.path.isfile(b) and os.path.isdir(a):
                return -1
            else:
                try:
                    if a[0] == '.':
                        a = a[1:]
                    if b[0] == '.':
                        b = b[1:]
                    return (1 if a.lower() > b.lower() else -1)
                except (IndexError, ValueError):
                    return (1 if a > b else -1)

        log.debug("FileFolderNavigator - populate_folder_items")

        if pyLessThan3:
            directory = directory.decode('utf-8')

        l = os.listdir(directory)
        l.sort(key=functools.cmp_to_key(_sort_files))

        # used to restore a valid path after a wrong edit in the path editor
        self._last_valid_path = directory
        # we remove the container avoiding graphic update adding items
        # this speeds up the navigation
        self.remove_child(self.itemContainer)
        # creation of a new instance of a itemContainer
        self.itemContainer = Container(width='100%', height='100%')
        self.itemContainer.style.update({'overflow-y': 'scroll', 'overflow-x': 'hidden'})

        for i in l:
            full_path = os.path.join(directory, i)
            is_folder = not os.path.isfile(full_path)
            if (not is_folder) and (not self.allow_file_selection):
                continue
            fi = FileFolderItem(full_path, i, is_folder)
            fi.onclick.connect(self.on_folder_item_click)  # navigation purpose
            fi.onselection.connect(self.on_folder_item_selected)  # selection purpose
            self.folderItems.append(fi)
            self.itemContainer.append(fi)
        self.append(self.itemContainer, key='items')  # replace the old widget

    def dir_go_back(self, widget):
        curpath = os.getcwd()  # backup the path
        try:
            os.chdir(self.pathEditor.get_text())
            os.chdir('..')
            self.chdir(os.getcwd())
        except Exception as e:
            self.pathEditor.set_text(self._last_valid_path)
            log.error('error changing directory', exc_info=True)
        os.chdir(curpath)  # restore the path

    def dir_go(self, widget):
        # when the GO button is pressed, it is supposed that the pathEditor is changed
        curpath = os.getcwd()  # backup the path
        try:
            os.chdir(self.pathEditor.get_text())
            self.chdir(os.getcwd())
        except Exception as e:
            log.error('error going to directory', exc_info=True)
            self.pathEditor.set_text(self._last_valid_path)
        os.chdir(curpath)  # restore the path

    def chdir(self, directory):
        self._selection_folder = directory
        curpath = os.getcwd()  # backup the path
        log.debug("FileFolderNavigator - chdir: %s" % directory)
        for c in self.folderItems:
            self.itemContainer.remove_child(c)  # remove the file and folders from the view
        self.folderItems = []
        self.selectionlist = []  # reset selected file list
        os.chdir(directory)
        directory = os.getcwd()
        self.disable_refresh()
        self.populate_folder_items(directory)
        self.enable_refresh()
        self.pathEditor.set_text(directory)
        self.currDir = directory
        os.chdir(curpath)  # restore the path

    @decorate_set_on_listener("(self, emitter, selected_item, selection_list)")
    @decorate_event
    def on_folder_item_selected(self, folderitem):
        """ This event occurs when an element in the list is selected
            Returns the newly selected element of type FileFolderItem(or None if it was not selectable) 
                 and the list of selected elements of type str.
        """
        if folderitem.isFolder and (not self.allow_folder_selection):
            folderitem.set_selected(False)
            self.on_folder_item_click(folderitem)
            return (None, self.selectionlist, )

        if not self.multiple_selection:
            self.selectionlist = []
            for c in self.folderItems:
                c.set_selected(False)
            folderitem.set_selected(True)
        log.debug("FileFolderNavigator - on_folder_item_click")
        # when an item is clicked it is added to the file selection list
        f = os.path.join(self.pathEditor.get_text(), folderitem.get_text())
        if f in self.selectionlist:
            self.selectionlist.remove(f)
        else:
            self.selectionlist.append(f)
        return (folderitem, self.selectionlist, )

    @decorate_set_on_listener("(self, emitter, clicked_item)")
    @decorate_event
    def on_folder_item_click(self, folderitem):
        """ This event occurs when a folder element is clicked.
            Returns the clicked element of type FileFolderItem.
        """
        log.debug("FileFolderNavigator - on_folder_item_dblclick")
        # when an item is clicked two time
        f = os.path.join(self.pathEditor.get_text(), folderitem.get_text())
        if not os.path.isfile(f):
            self.chdir(f)
        return (folderitem, )

    def get_selected_filefolders(self):
        return self.selectionlist


class FileFolderItem(Container):
    """FileFolderItem widget for the FileFolderNavigator"""
    path_and_filename = None #the complete path and filename
    def __init__(self, path_and_filename, text, is_folder=False, *args, **kwargs):
        super(FileFolderItem, self).__init__(*args, **kwargs)
        self.path_and_filename = path_and_filename
        self.isFolder = is_folder
        self.icon = Widget(_class='FileFolderItemIcon')
        # the icon click activates the onselection event, that is propagates to registered listener
        if is_folder:
            self.icon.onclick.connect(self.onclick)
        else:
            self.icon.onclick.connect(self.onselection)
        icon_file = '/res:folder.png' if is_folder else '/res:file.png'
        self.icon.css_background_image = "url('%s')" % icon_file
        self.label = Label(text)
        self.label.onclick.connect(self.onselection)
        self.append(self.icon, key='icon')
        self.append(self.label, key='text')
        self.selected = False

    def set_selected(self, selected):
        self.selected = selected
        self.label.css_font_weight = 'bold' if self.selected else 'normal'

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event
    def onclick(self, widget):
        return super(FileFolderItem, self).onclick()

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event
    def onselection(self, widget):
        self.set_selected(not self.selected)
        return ()

    def set_text(self, t):
        self.children['text'].set_text(t)

    def get_text(self):
        return self.children['text'].get_text()


class FileSelectionDialog(GenericDialog):
    """file selection dialog, it opens a new webpage allows the OK/CANCEL functionality
    implementing the "confirm_value" and "cancel_dialog" events."""

    def __init__(self, title='File dialog', message='Select files and folders',
                 multiple_selection=True, selection_folder='.',
                 allow_file_selection=True, allow_folder_selection=True, **kwargs):
        super(FileSelectionDialog, self).__init__(title, message, **kwargs)

        self.css_width = '475px'
        self.fileFolderNavigator = FileFolderNavigator(multiple_selection, selection_folder,
                                                       allow_file_selection,
                                                       allow_folder_selection, width="100%", height="330px")
        self.add_field('fileFolderNavigator', self.fileFolderNavigator)
        self.confirm_dialog.connect(self.confirm_value)

    @decorate_set_on_listener("(self, emitter, fileList)")
    @decorate_event
    def confirm_value(self, widget):
        """event called pressing on OK button.
           propagates the string content of the input field
        """
        self.hide()
        params = (self.fileFolderNavigator.get_selection_list(),)
        return params


class MenuBar(Container):

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(MenuBar, self).__init__(*args, **kwargs)
        self.type = 'nav'
        self.set_layout_orientation(Container.LAYOUT_HORIZONTAL)


class Menu(Container):
    """Menu widget can contain MenuItem."""

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(Menu, self).__init__(layout_orientation=Container.LAYOUT_HORIZONTAL, *args, **kwargs)
        self.type = 'ul'


class MenuItem(Container, _MixinTextualWidget):
    """MenuItem widget can contain other MenuItem."""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str):
            kwargs: See Container.__init__()
        """
        self.sub_container = Menu()
        super(MenuItem, self).__init__(*args, **kwargs)
        super(MenuItem, self).append(self.sub_container, key='subcontainer')
        self.type = 'li'
        self.set_text(text)

        self.attributes['tabindex'] = '0'

    def append(self, value, key=''):

        return self.sub_container.append(value, key=key)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');document.activeElement.blur();")
    def onclick(self):
        """Called when the Widget gets clicked by the user with the left mouse button."""
        return ()


class TreeView(Container):
    """TreeView widget can contain TreeItem."""

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Container.__init__()
        """
        super(TreeView, self).__init__(*args, **kwargs)
        self.type = 'ul'


class TreeItem(Container, _MixinTextualWidget):
    """TreeItem widget can contain other TreeItem."""

    def __init__(self, text='', *args, **kwargs):
        """
        Args:
            text (str):
            kwargs: See Widget.__init__()
        """
        super(TreeItem, self).__init__(*args, **kwargs)
        self.sub_container = None
        self.type = 'li'
        self.set_text(text)
        self.treeopen = False
        self.attributes['treeopen'] = 'false'
        self.attributes['has-subtree'] = 'false'
        self.onclick.do(None, js_stop_propagation=True)
        
    def append(self, value, key=''):
        if self.sub_container is None:
            self.attributes['has-subtree'] = 'true'
            self.sub_container = TreeView()
            super(TreeItem, self).append(self.sub_container, key='subcontainer')
        return self.sub_container.append(value, key=key)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onclick(self):
        self.treeopen = not self.treeopen
        if self.treeopen:
            self.attributes['treeopen'] = 'true'
        else:
            self.attributes['treeopen'] = 'false'
        return super(TreeItem, self).onclick()


class FileUploader(Container):
    """
    FileUploader widget:
        allows to upload multiple files to a specified folder.
        implements the onsuccess and onfailed events.
    """
    @property
    @editor_attribute_decorator("WidgetSpecific",'''If True multiple files can be 
        selected at the same time''', bool, {})
    def multiple_selection_allowed(self): return ('multiple' in self.__dict__.keys())
    @multiple_selection_allowed.setter
    def multiple_selection_allowed(self, value): 
        if value:
            self.__dict__["multiple"] = "multiple"
        else:
            if 'multiple' in self.__dict__.keys():
                del self.__dict__["multiple"]

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Defines the path where to save the file''', str, {})
    def savepath(self): return self._savepath
    @savepath.setter
    def savepath(self, value): 
        self._savepath = value

    def __init__(self, savepath='./', multiple_selection_allowed=False, accepted_files='*.*', *args, **kwargs):
        super(FileUploader, self).__init__(*args, **kwargs)
        self._savepath = savepath
        self._multiple_selection_allowed = multiple_selection_allowed
        self.type = 'input'
        self.attributes['type'] = 'file'
        if multiple_selection_allowed:
            self.attributes['multiple'] = 'multiple'
        self.attributes['accept'] = accepted_files
        self.EVENT_ON_SUCCESS = 'onsuccess'
        self.EVENT_ON_FAILED = 'onfailed'
        self.EVENT_ON_DATA = 'ondata'

        self.attributes[self.EVENT_ONCHANGE] = \
            "var files = this.files;" \
            "for(var i=0; i<files.length; i++){" \
            "remi.uploadFile('%(id)s','%(evt_success)s','%(evt_failed)s','%(evt_data)s',files[i]);}" % {
                'id': self.identifier, 'evt_success': self.EVENT_ON_SUCCESS, 'evt_failed': self.EVENT_ON_FAILED,
                'evt_data': self.EVENT_ON_DATA}

    @decorate_set_on_listener("(self, emitter, filename)")
    @decorate_event
    def onsuccess(self, filename):
        return (filename, )

    @decorate_set_on_listener("(self, emitter, filename)")
    @decorate_event
    def onfailed(self, filename):
        return (filename, )

    @decorate_set_on_listener("(self, emitter, filedata, filename)")
    @decorate_event
    def ondata(self, filedata, filename):
        with open(os.path.join(self._savepath, filename), 'wb') as f:
            f.write(filedata)
        return (filedata, filename)

    @decorate_set_on_listener("(self, emitter, filename, loaded, total)")
    @decorate_event
    def onprogress(self, filename, loaded, total):
        """
        Args:
            filename (str): the file name that is uploading
            loaded (int): loaded bytes
            total (int): file size in bytes
        """
        return (filename, int(loaded), int(total))


class FileDownloader(Container, _MixinTextualWidget):
    """FileDownloader widget. Allows to start a file download."""

    def __init__(self, text, filename, path_separator='/', *args, **kwargs):
        super(FileDownloader, self).__init__(*args, **kwargs)
        self.type = 'a'
        self.attributes['download'] = os.path.basename(filename)
        self.attributes['href'] = "/%s/download" % self.identifier
        self.set_text(text)
        self._filename = filename
        self._path_separator = path_separator

    def download(self):
        with open(self._filename, 'rb') as f:
            content = f.read()
        filename = quote(os.path.basename(self._filename))
        headers = {'Content-type': 'application/octet-stream',
                   'Content-Disposition': 'attachment; filename="{0}"; filename*=UTF-8\'\'{0}'.format(filename)}
        return [content, headers]


class Link(Container, _MixinTextualWidget):
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Link url''', str, {})
    def attr_href(self): return self.attributes.get('href', '')
    @attr_href.setter
    def attr_href(self, value): self.attributes['href'] = str(value)

    def __init__(self, url='', text='', open_new_window=True, *args, **kwargs):
        super(Link, self).__init__(*args, **kwargs)
        self.type = 'a'
        self.attributes['href'] = url
        if open_new_window:
            self.attributes['target'] = "_blank"
        self.set_text(text)

    def get_url(self):
        return self.attributes['href']


class VideoPlayer(Widget):
    # some constants for the events
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Video url''', str, {})
    def attr_src(self): return self.attributes.get('src', '')
    @attr_src.setter
    def attr_src(self, value): self.attributes['src'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Video poster img''', 'base64_image', {})
    def attr_poster(self): return self.attributes.get('poster', '')
    @attr_poster.setter
    def attr_poster(self, value): self.attributes['poster'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Video autoplay''', bool, {})
    def attr_autoplay(self): return self.attributes.get('autoplay', '')
    @attr_autoplay.setter
    def attr_autoplay(self, value): self.attributes['autoplay'] = str(value).lower()

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Video loop''', bool, {})
    def attr_loop(self): return self.attributes.get('loop', '')
    @attr_loop.setter
    def attr_loop(self, value): self.attributes['loop'] = str(value).lower()

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Video type''', str, {})
    def attr_type(self): return self.attributes.get('type', '')
    @attr_type.setter
    def attr_type(self, value): self.attributes['type'] = str(value).lower()

    def __init__(self, video='', poster=None, autoplay=False, loop=False, *args, **kwargs):
        super(VideoPlayer, self).__init__(*args, **kwargs)
        self.type = 'video'
        self.attributes['src'] = video
        self.attributes['preload'] = 'auto'
        self.attributes['controls'] = None
        self.attributes['poster'] = poster
        self.set_autoplay(autoplay)
        self.set_loop(loop)

    def set_autoplay(self, autoplay):
        if autoplay:
            self.attributes['autoplay'] = 'true'
        else:
            self.attributes.pop('autoplay', None)

    def set_loop(self, loop):
        """Sets the VideoPlayer to restart video when finished.

        Note: If set as True the event onended will not fire."""

        if loop:
            self.attributes['loop'] = 'true'
        else:
            self.attributes.pop('loop', None)

    @decorate_set_on_listener("(self, emitter)")
    @decorate_event_js("remi.sendCallback('%(emitter_identifier)s','%(event_name)s');")
    def onended(self):
        """Called when the media has been played and reached the end."""
        return ()


class _MixinSvgStroke():
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Color for svg elements.''', 'ColorPicker', {})
    def attr_stroke(self): return self.attributes.get('stroke', None)
    @attr_stroke.setter
    def attr_stroke(self, value): self.attributes['stroke'] = str(value)
    @attr_stroke.deleter
    def attr_stroke(self): del self.attributes['stroke'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Stroke width for svg elements.''', float, {'possible_values': '', 'min': 0.0, 'max': 10000.0, 'default': 1.0, 'step': 0.1})
    def attr_stroke_width(self): return self.attributes.get('stroke-width', None)
    @attr_stroke_width.setter
    def attr_stroke_width(self, value): self.attributes['stroke-width'] = str(value)
    @attr_stroke_width.deleter
    def attr_stroke_width(self): del self.attributes['stroke-width'] 

    def set_stroke(self, width=1, color='black'):
        """Sets the stroke properties.

        Args:
            width (float): stroke width
            color (str): stroke color
        """
        self.attr_stroke = color
        self.attr_stroke_width = str(width)


class _MixinTransformable():
    @property
    @editor_attribute_decorator("Transformation", '''Transform commands (i.e. rotate(45), translate(30,100)).''', str, {})
    def css_transform(self): return self.style.get('transform', None)
    @css_transform.setter
    def css_transform(self, value): self.style['transform'] = str(value)
    @css_transform.deleter
    def css_transform(self): del self.style['transform'] 

    @property
    @editor_attribute_decorator("Transformation", '''Transform origin as percent or absolute x,y pair value or ['center','top','bottom','left','right'] .''', str, {})
    def css_transform_origin(self): return self.style.get('transform-origin', None)
    @css_transform_origin.setter
    def css_transform_origin(self, value): self.style['transform-origin'] = str(value)
    @css_transform_origin.deleter
    def css_transform_origin(self): del self.style['transform-origin'] 

    @property
    @editor_attribute_decorator("Transformation", '''Alters the behaviour of tranform and tranform-origin by defining the transform box.''', 'DropDown', {'possible_values': ('content-box','border-box','fill-box','stroke-box','view-box')})
    def css_transform_box(self): return self.style.get('transform-box', None)
    @css_transform_box.setter
    def css_transform_box(self, value): self.style['transform-box'] = str(value)
    @css_transform_box.deleter
    def css_transform_box(self): del self.style['transform-box'] 


class _MixinSvgFill():
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Fill color for svg elements.''', 'ColorPicker', {})
    def attr_fill(self): return self.attributes.get('fill', None)
    @attr_fill.setter
    def attr_fill(self, value): self.attributes['fill'] = str(value)
    @attr_fill.deleter
    def attr_fill(self): del self.attributes['fill'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Fill opacity for svg elements.''', float, {'possible_values': '', 'min': 0.0, 'max': 1.0, 'default': 1.0, 'step': 0.1})
    def attr_fill_opacity(self): return self.attributes.get('fill-opacity', None)
    @attr_fill_opacity.setter
    def attr_fill_opacity(self, value): self.attributes['fill-opacity'] = str(value)
    @attr_fill_opacity.deleter
    def attr_fill_opacity(self): del self.attributes['fill-opacity'] 

    def set_fill(self, color='black'):
        """Sets the fill color.

        Args:
            color (str): stroke color
        """
        self.attr_fill = color


class _MixinSvgPosition():
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Coordinate for Svg element.''', float, {'possible_values': '', 'min': -65635.0, 'max': 65635.0, 'default': 1.0, 'step': 0.1})
    def attr_x(self): return self.attributes.get('x', '0')
    @attr_x.setter
    def attr_x(self, value): self.attributes['x'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Coordinate for Svg element.''', float, {'possible_values': '', 'min': -65635.0, 'max': 65635.0, 'default': 1.0, 'step': 0.1})
    def attr_y(self): return self.attributes.get('y', '0')
    @attr_y.setter
    def attr_y(self, value): self.attributes['y'] = str(value)

    def set_position(self, x, y):
        """Sets the shape position.

        Args:
            x (float): the x coordinate
            y (float): the y coordinate
        """
        self.attr_x = x
        self.attr_y = y


class _MixinSvgSize():
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Width for Svg element.''', float, {'possible_values': '', 'min': 0.0, 'max': 65635.0, 'default': 1.0, 'step': 0.1})
    def attr_width(self): return self.attributes.get('width', '100')
    @attr_width.setter
    def attr_width(self, value): self.attributes['width'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Height for Svg element.''', float, {'possible_values': '', 'min': 0.0, 'max': 65635.0, 'default': 1.0, 'step': 0.1})
    def attr_height(self): return self.attributes.get('height', '100')
    @attr_height.setter
    def attr_height(self, value): self.attributes['height'] = str(value)

    def set_size(self, w, h):
        """ Sets the rectangle size.

        Args:
            w (int): width of the rectangle
            h (int): height of the rectangle
        """
        self.attr_width = w
        self.attr_height = h


class SvgStop(Tag):
    """ """
     
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient color''', 'ColorPicker', {})
    def css_stop_color(self): return self.style.get('stop-color', None)
    @css_stop_color.setter
    def css_stop_color(self, value): self.style['stop-color'] = str(value)
    @css_stop_color.deleter
    def css_stop_color(self): del self.style['stop-color']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''The opacity property sets the opacity level for the gradient.
    The opacity-level describes the transparency-level, where 1 is not transparent at all, 0.5 is 50% see-through, and 0 is completely transparent.''', float, {'possible_values': '', 'min': 0.0, 'max': 1.0, 'default': 1.0, 'step': 0.1})
    def css_stop_opactity(self): return self.style.get('stop-opacity', None)
    @css_stop_opactity.setter
    def css_stop_opactity(self, value): self.style['stop-opacity'] = str(value)
    @css_stop_opactity.deleter
    def css_stop_opactity(self): del self.style['stop-opacity']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''The offset value for the gradient stop. It is in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_offset(self): return self.attributes.get('offset', None)
    @attr_offset.setter
    def attr_offset(self, value): self.attributes['offset'] = str(value)

    def __init__(self, offset='0%', color="rgb(255,255,0)", opacity=1.0, *args, **kwargs):
        super(SvgStop, self).__init__(*args, **kwargs)
        self.type = 'stop'
        self.attr_offset = offset
        self.css_stop_color = color
        self.css_stop_opactity = opacity


class SvgGradientLinear(Tag):
    """ """
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_x1(self): return self.attributes.get('x1', None)
    @attr_x1.setter
    def attr_x1(self, value): 
        self.attributes['x1'] = str(value)
        if not self.attributes['x1'][-1] == '%':
            self.attributes['x1'] = self.attributes['x1'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_y1(self): return self.attributes.get('y1', None)
    @attr_y1.setter
    def attr_y1(self, value): 
        self.attributes['y1'] = str(value)
        if not self.attributes['y1'][-1] == '%':
            self.attributes['y1'] = self.attributes['y1'] + '%'
    
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_x2(self): return self.attributes.get('x2', None)
    @attr_x2.setter
    def attr_x2(self, value): 
        self.attributes['x2'] = str(value)
        if not self.attributes['x2'][-1] == '%':
            self.attributes['x2'] = self.attributes['x2'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_y2(self): return self.attributes.get('y2', None)
    @attr_y2.setter
    def attr_y2(self, value): 
        self.attributes['y2'] = str(value)
        if not self.attributes['y2'][-1] == '%':
            self.attributes['y2'] = self.attributes['y2'] + '%'

    def __init__(self, x1, y1, x2, y2, *args, **kwargs):
        super(SvgGradientLinear, self).__init__(*args, **kwargs)
        self.type = 'linearGradient'
        self.attr_x1 = x1
        self.attr_y1 = y1
        self.attr_x2 = x2
        self.attr_y2 = y2


class SvgGradientRadial(Tag):
    """ """
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_cx(self): return self.attributes.get('cx', None)
    @attr_cx.setter
    def attr_cx(self, value): 
        self.attributes['cx'] = str(value)
        if not self.attributes['cx'][-1] == '%':
            self.attributes['cx'] = self.attributes['cx'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_cy(self): return self.attributes.get('cy', None)
    @attr_cy.setter
    def attr_cy(self, value): 
        self.attributes['cy'] = str(value)
        if not self.attributes['cy'][-1] == '%':
            self.attributes['cy'] = self.attributes['cy'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_fx(self): return self.attributes.get('fx', None)
    @attr_fx.setter
    def attr_fx(self, value): 
        self.attributes['fx'] = str(value)
        if not self.attributes['fx'][-1] == '%':
            self.attributes['fx'] = self.attributes['fx'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient coordinate value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_fy(self): return self.attributes.get('fy', None)
    @attr_fy.setter
    def attr_fy(self, value): 
        self.attributes['fy'] = str(value)
        if not self.attributes['fy'][-1] == '%':
            self.attributes['fy'] = self.attributes['fy'] + '%'

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Gradient radius value. It is expressed in percentage''', float, {'possible_values': '', 'min': 0, 'max': 100, 'default': 0, 'step': 1})
    def attr_r(self): return self.attributes.get('r', None)
    @attr_r.setter
    def attr_r(self, value): 
        self.attributes['r'] = str(value)
        if not self.attributes['r'][-1] == '%':
            self.attributes['r'] = self.attributes['r'] + '%'

    def __init__(self, cx="20%", cy="30%", r="30%", fx="50%", fy="50%", *args, **kwargs):
        super(SvgGradientRadial, self).__init__(*args, **kwargs)
        self.type = 'radialGradient'
        self.attr_cx = cx
        self.attr_cy = cy
        self.attr_fx = fx
        self.attr_fy = fy
        self.attr_r = r


class SvgDefs(Tag):
    """ """
    def __init__(self, *args, **kwargs):
        super(SvgDefs, self).__init__(*args, **kwargs)
        self.type = 'defs'
    

class Svg(Container):
    """svg widget - is a container for graphic widgets such as SvgCircle, SvgLine and so on."""
    @property
    @editor_attribute_decorator("WidgetSpecific",'''preserveAspectRatio' ''', 'DropDown', {'possible_values': ('none','xMinYMin meet','xMidYMin meet','xMaxYMin meet','xMinYMid meet','xMidYMid meet','xMaxYMid meet','xMinYMax meet','xMidYMax meet','xMaxYMax meet','xMinYMin slice','xMidYMin slice','xMaxYMin slice','xMinYMid slice','xMidYMid slice','xMaxYMid slice','xMinYMax slice','xMidYMax slice','xMaxYMax slice')})
    def attr_preserveAspectRatio(self): return self.attributes.get('preserveAspectRatio', None)
    @attr_preserveAspectRatio.setter
    def attr_preserveAspectRatio(self, value): self.attributes['preserveAspectRatio'] = str(value)
    @attr_preserveAspectRatio.deleter
    def attr_preserveAspectRatio(self): del self.attributes['preserveAspectRatio'] 

    @property
    @editor_attribute_decorator("WidgetSpecific",'''viewBox of the svg drawing. es='x, y, width, height' ''', 'str', {})
    def attr_viewBox(self): return self.attributes.get('viewBox', None)
    @attr_viewBox.setter
    def attr_viewBox(self, value): self.attributes['viewBox'] = str(value)
    @attr_viewBox.deleter
    def attr_viewBox(self): del self.attributes['viewBox'] 

    def __init__(self, *args, **kwargs):
        """
        Args:
            kwargs: See Widget.__init__()
        """
        super(Svg, self).__init__(*args, **kwargs)
        self.type = 'svg'
        
    def set_viewbox(self, x, y, w, h):
        """Sets the origin and size of the viewbox, describing a virtual view area.

        Args:
            x (int): x coordinate of the viewbox origin
            y (int): y coordinate of the viewbox origin
            w (int): width of the viewBox
            h (int): height of the viewBox
        """
        self.attr_viewBox = "%s %s %s %s" % (x, y, w, h)
        self.attr_preserveAspectRatio = 'none'


class SvgSubcontainer(Svg, _MixinSvgPosition, _MixinSvgSize):
    """svg widget to nest within another Svg element- is a container for graphic widgets such as SvgCircle, SvgLine and so on."""

    def __init__(self, x=0, y=0, width=100, height=100, *args, **kwargs):
        """
        Args:
            width (int): the viewBox width in pixel
            height (int): the viewBox height in pixel
            kwargs: See Widget.__init__()
        """
        super(SvgSubcontainer, self).__init__(*args, **kwargs)
        self.type = 'svg'
        self.set_position(x, y)
        _MixinSvgSize.set_size(self, width, height)


class SvgGroup(Container, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    """svg group - a non visible container for svg widgets,
        this have to be appended into Svg elements."""
    def __init__(self, *args, **kwargs):
        super(SvgGroup, self).__init__(*args, **kwargs)
        self.type = 'g'


class SvgRectangle(Widget, _MixinSvgPosition, _MixinSvgSize, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Horizontal round corners value.''', float, {'possible_values': '', 'min': 0.0, 'max': 10000.0, 'default': 1.0, 'step': 0.1})
    def attr_round_corners_h(self): return self.attributes.get('rx', '0')
    @attr_round_corners_h.setter
    def attr_round_corners_h(self, value): self.attributes['rx'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Vertical round corners value. Defaults to attr_round_corners_h.''', float, {'possible_values': '', 'min': 0.0, 'max': 10000.0, 'default': 1.0, 'step': 0.1})
    def attr_round_corners_y(self): return self.attributes.get('ry', '0')
    @attr_round_corners_y.setter
    def attr_round_corners_y(self, value): self.attributes['ry'] = str(value)

    def __init__(self, x=0, y=0, w=100, h=100, *args, **kwargs):
        """
        Args:
            x (float): the x coordinate of the top left corner of the rectangle
            y (float): the y coordinate of the top left corner of the rectangle
            w (float): width of the rectangle
            h (float): height of the rectangle
            kwargs: See Widget.__init__()
        """
        super(SvgRectangle, self).__init__(*args, **kwargs)
        self.set_position(x, y)
        _MixinSvgSize.set_size(self, w, h)
        self.type = 'rect'


class SvgImage(Widget, _MixinSvgPosition, _MixinSvgSize, _MixinTransformable):
    """svg image - a raster image element for svg graphics,
        this have to be appended into Svg elements."""
    @property
    @editor_attribute_decorator("WidgetSpecific", '''preserveAspectRatio' ''', 'DropDown', {'possible_values': ('none','xMinYMin meet','xMidYMin meet','xMaxYMin meet','xMinYMid meet','xMidYMid meet','xMaxYMid meet','xMinYMax meet','xMidYMax meet','xMaxYMax meet','xMinYMin slice','xMidYMin slice','xMaxYMin slice','xMinYMid slice','xMidYMid slice','xMaxYMid slice','xMinYMax slice','xMidYMax slice','xMaxYMax slice')})
    def attr_preserveAspectRatio(self): return self.attributes.get('preserveAspectRatio', None)
    @attr_preserveAspectRatio.setter
    def attr_preserveAspectRatio(self, value): self.attributes['preserveAspectRatio'] = str(value)
    @attr_preserveAspectRatio.deleter
    def attr_preserveAspectRatio(self): del self.attributes['preserveAspectRatio'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Image data or url  or a base64 data string, html attribute xlink:href''', 'base64_image', {})
    def image_data(self): return self.attributes.get('xlink:href', '')
    @image_data.setter
    def image_data(self, value): self.attributes['xlink:href'] = str(value)
    @image_data.deleter
    def image_data(self): del self.attributes['xlink:href'] 

    def __init__(self, image_data='', x=0, y=0, w=100, h=100, *args, **kwargs):
        """
        Args:
            image_data (str): an url to an image
            x (float): the x coordinate of the top left corner of the rectangle
            y (float): the y coordinate of the top left corner of the rectangle
            w (float): width of the rectangle
            h (float): height of the rectangle
            kwargs: See Widget.__init__()
        """
        super(SvgImage, self).__init__(*args, **kwargs)
        self.type = 'image'
        self.image_data = image_data
        self.set_position(x, y)
        _MixinSvgSize.set_size(self, w, h)


class SvgCircle(Widget, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Center coordinate for SvgCircle.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_cx(self): return self.attributes.get('cx', None)
    @attr_cx.setter
    def attr_cx(self, value): self.attributes['cx'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Center coordinate for SvgCircle.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_cy(self): return self.attributes.get('cy', None)
    @attr_cy.setter
    def attr_cy(self, value): self.attributes['cy'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Radius of SvgCircle.''', float, {'possible_values': '', 'min': 0.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_r(self): return self.attributes.get('r', None)
    @attr_r.setter
    def attr_r(self, value): self.attributes['r'] = str(value)

    def __init__(self, x=0, y=0, radius=50, *args, **kwargs):
        """
        Args:
            x (float): the x center point of the circle
            y (float): the y center point of the circle
            radius (float): the circle radius
            kwargs: See Widget.__init__()
        """
        super(SvgCircle, self).__init__(*args, **kwargs)
        self.set_position(x, y)
        self.set_radius(radius)
        self.type = 'circle'

    def set_radius(self, radius):
        """Sets the circle radius.

        Args:
            radius (int): the circle radius
        """
        self.attr_r = radius

    def set_position(self, x, y):
        """Sets the circle position.

        Args:
            x (int): the x coordinate
            y (int): the y coordinate
        """
        self.attr_cx = str(x)
        self.attr_cy = str(y)


class SvgEllipse(Widget, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Coordinate for SvgEllipse.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_cx(self): return self.attributes.get('cx', None)
    @attr_cx.setter
    def attr_cx(self, value): self.attributes['cx'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Coordinate for SvgEllipse.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_cy(self): return self.attributes.get('cy', None)
    @attr_cy.setter
    def attr_cy(self, value): self.attributes['cy'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Radius of SvgEllipse.''', float, {'possible_values': '', 'min': 0.0, 'max': 10000.0, 'default': 1.0, 'step': 0.1})
    def attr_rx(self): return self.attributes.get('rx', None)
    @attr_rx.setter
    def attr_rx(self, value): self.attributes['rx'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''Radius of SvgEllipse.''', float, {'possible_values': '', 'min': 0.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_ry(self): return self.attributes.get('ry', None)
    @attr_ry.setter
    def attr_ry(self, value): self.attributes['ry'] = str(value)

    def __init__(self, x=0, y=0, rx=50, ry=30, *args, **kwargs):
        """
        Args:
            x (float): the x center point of the ellipse
            y (float): the y center point of the ellipse
            rx (float): the ellipse radius
            ry (float): the ellipse radius
            kwargs: See Widget.__init__()
        """
        super(SvgEllipse, self).__init__(*args, **kwargs)
        self.set_position(x, y)
        self.set_radius(rx, ry)
        self.type = 'ellipse'

    def set_radius(self, rx, ry):
        """Sets the ellipse radius.

        Args:
            rx (int): the ellipse radius
            ry (int): the ellipse radius
        """
        self.attr_rx = rx
        self.attr_ry = ry

    def set_position(self, x, y):
        """Sets the ellipse position.

        Args:
            x (int): the x coordinate
            y (int): the y coordinate
        """
        self.attr_cx = str(x)
        self.attr_cy = str(y)


class SvgLine(Widget, _MixinSvgStroke, _MixinTransformable):
    @property
    @editor_attribute_decorator("WidgetSpecific",'''P1 coordinate for SvgLine.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_x1(self): return self.attributes.get('x1', None)
    @attr_x1.setter
    def attr_x1(self, value): self.attributes['x1'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''P1 coordinate for SvgLine.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_y1(self): return self.attributes.get('y1', None)
    @attr_y1.setter
    def attr_y1(self, value): self.attributes['y1'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''P2 coordinate for SvgLine.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_x2(self): return self.attributes.get('x2', None)
    @attr_x2.setter
    def attr_x2(self, value): self.attributes['x2'] = str(value)

    @property
    @editor_attribute_decorator("WidgetSpecific",'''P2 coordinate for SvgLine.''', float, {'possible_values': '', 'min': -65535.0, 'max': 65535.0, 'default': 1.0, 'step': 0.1})
    def attr_y2(self): return self.attributes.get('y2', None)
    @attr_y2.setter
    def attr_y2(self, value): self.attributes['y2'] = str(value)

    def __init__(self, x1=0, y1=0, x2=50, y2=50, *args, **kwargs):
        super(SvgLine, self).__init__(*args, **kwargs)
        self.set_coords(x1, y1, x2, y2)
        self.type = 'line'

    def set_coords(self, x1, y1, x2, y2):
        self.set_p1(x1, y1)
        self.set_p2(x2, y2)

    def set_p1(self, x1, y1):
        self.attr_x1 = x1
        self.attr_y1 = y1

    def set_p2(self, x2, y2):
        self.attr_x2 = x2
        self.attr_y2 = y2


class SvgPolyline(Widget, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    @property
    @editor_attribute_decorator("WidgetSpecific",'''Defines the maximum values count.''', int, {'possible_values': '', 'min': 0, 'max': 65535, 'default': 0, 'step': 1})
    def maxlen(self): return self.__maxlen
    @maxlen.setter
    def maxlen(self, value): 
        self.__maxlen = int(value)
        self.coordsX = collections.deque(maxlen=self.__maxlen)
        self.coordsY = collections.deque(maxlen=self.__maxlen)

    def __init__(self, _maxlen=1000, *args, **kwargs):
        self.__maxlen = 0
        super(SvgPolyline, self).__init__(*args, **kwargs)
        self.type = 'polyline'
        self.maxlen = _maxlen  # no limit
        self.attributes['points'] = ''
        self.attributes['vector-effect'] = 'non-scaling-stroke'

    def add_coord(self, x, y):
        if len(self.coordsX) == self.maxlen:
            spacepos = self.attributes['points'].find(' ')
            if spacepos > 0:
                self.attributes['points'] = self.attributes['points'][spacepos + 1:]
        self.coordsX.append(x)
        self.coordsY.append(y)
        self.attributes['points'] += "%s,%s " % (x, y)


class SvgPolygon(SvgPolyline, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    def __init__(self, _maxlen=1000, *args, **kwargs):
        super(SvgPolygon, self).__init__(_maxlen, *args, **kwargs)
        self.type = 'polygon'


class SvgText(Widget, _MixinSvgPosition, _MixinSvgStroke, _MixinSvgFill, _MixinTextualWidget, _MixinTransformable):

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Length for svg text elements.''', int, {'possible_values': '', 'min': 0.0, 'max': 10000.0, 'default': 1.0, 'step': 0.1})
    def attr_textLength(self): return self.attributes.get('textLength', None)
    @attr_textLength.setter
    def attr_textLength(self, value): self.attributes['textLength'] = str(value)
    @attr_textLength.deleter
    def attr_textLength(self): del self.attributes['textLength'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Controls how text is stretched to fit the length.''', 'DropDown', {'possible_values': ('spacing','spacingAndGlyphs')})
    def attr_lengthAdjust(self): return self.attributes.get('lengthAdjust', None)
    @attr_lengthAdjust.setter
    def attr_lengthAdjust(self, value): self.attributes['lengthAdjust'] = str(value)
    @attr_lengthAdjust.deleter
    def attr_lengthAdjust(self): del self.attributes['lengthAdjust'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Rotation angle for svg elements.''', float, {'possible_values': '', 'min': -360.0, 'max': 360.0, 'default': 1.0, 'step': 0.1})
    def attr_rotate(self): return self.attributes.get('rotate', None)
    @attr_rotate.setter
    def attr_rotate(self, value): self.attributes['rotate'] = str(value)
    @attr_rotate.deleter
    def attr_rotate(self): del self.attributes['rotate'] 

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Description.''', 'DropDown', {'possible_values': ('start', 'middle', 'end')})
    def attr_text_anchor(self): return self.style.get('text-anchor', None)
    @attr_text_anchor.setter
    def attr_text_anchor(self, value): self.style['text-anchor'] = str(value)
    @attr_text_anchor.deleter
    def attr_text_anchor(self): del self.style['text-anchor']

    @property
    @editor_attribute_decorator("WidgetSpecific", '''Description.''', 'DropDown', {'possible_values': ('auto', 'text-bottom', 'alphabetic', 'ideographic', 'middle', 'central', 'mathematical', 'hanging', 'text-top')})
    def attr_dominant_baseline(self): return self.style.get('dominant-baseline', None)
    @attr_dominant_baseline.setter
    def attr_dominant_baseline(self, value): self.style['dominant-baseline'] = str(value)
    @attr_dominant_baseline.deleter
    def attr_dominant_baseline(self): del self.style['dominant-baseline']

    def __init__(self, x=10, y=10, text='svg text', *args, **kwargs):
        super(SvgText, self).__init__(*args, **kwargs)
        self.type = 'text'
        self.set_position(x, y)
        self.set_text(text)


class SvgPath(Widget, _MixinSvgStroke, _MixinSvgFill, _MixinTransformable):
    @property
    @editor_attribute_decorator("WidgetSpecific", '''Instructions for SvgPath.''', str, {})
    def attr_d(self): return self.attributes.get('d', None)
    @attr_d.setter
    def attr_d(self, value): self.attributes['d'] = str(value)

    def __init__(self, path_value='', *args, **kwargs):
        super(SvgPath, self).__init__(*args, **kwargs)
        self.type = 'path'
        self.attributes['d'] = path_value

    def add_position(self, x, y):
        self.attributes['d'] = self.attributes['d'] + "M %s %s" % (x, y)

    def add_arc(self, x, y, rx, ry, x_axis_rotation, large_arc_flag, sweep_flag):
        # A rx ry x-axis-rotation large-arc-flag sweep-flag x y
        self.attributes['d'] = self.attributes['d'] + "A %(rx)s %(ry)s, %(x-axis-rotation)s, %(large-arc-flag)s, %(sweep-flag)s, %(x)s %(y)s"%{'x':x,
            'y': y, 'rx': rx, 'ry': ry, 'x-axis-rotation': x_axis_rotation, 'large-arc-flag': large_arc_flag, 'sweep-flag': sweep_flag}


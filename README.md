[![Build Status](https://travis-ci.com/dddomodossola/remi.svg?branch=master)](https://travis-ci.com/dddomodossola/remi)

<p align="center">
    <img src="https://raw.githubusercontent.com/rawpython/remi/master/remi/res/logo.png" width="430">
</p>

<h2 align="center" style="font-weight:bolder">
    GUI library for your Python applications
</h2>

<p align="center" style="white-space:pre">
Remi is a GUI library for Python applications that gets rendered in web browsers.
This allows you to access your interface locally and remotely.
</p>


Proud to be sponsored by
===
<p align="center">
<a href="https://www.simracingstudio.com/" style="font-size:25px" >
<img src="https://static.wixstatic.com/media/092827_44a73f6751144b85af49c280304c9f25~mv2.png/v1/crop/x_0,y_37,w_4349,h_2313/fill/w_155,h_83,al_c,q_85,usm_0.66_1.00_0.01/srs-logo-whitebackground-slogan.webp" width="250">
</a>
<p>
<p align="center">
<a href="https://github.com/PySimpleGUI" style="font-size:25px;right:0px;">
<img src="https://raw.githubusercontent.com/PySimpleGUI/PySimpleGUI/master/images/for_readme/Logo%20with%20text%20for%20GitHub%20Top.png" width="200">
</a>
<p>

Do you need support?
===
<p align="center">
<a href="https://www.reddit.com/r/RemiGUI" style="font-size:25px">Reddit - (subreddit RemiGUI)</a>
</p>


There is also a **drag n drop GUI Editor**. Look at the [Editor](https://github.com/rawpython/remi/tree/master/editor) subfolder to download your copy.

<a href="https://vimeo.com/517626422" style="font-size:25px">A demonstrative video from the great REVVEN labs</a>

<p align="center">


Getting Started
===
For a **stable** version:
```
pip install remi
```

For the most updated **experimental** version [Download](https://github.com/rawpython/remi/archive/master.zip) or check out Remi from git and install

```
python setup.py install
```
or install directly using pip

```
pip install git+https://github.com/rawpython/remi.git
```

Then start the test script (download it from github https://github.com/rawpython/remi/blob/master/examples/widgets_overview_app.py):
```
python widgets_overview_app.py
```


Remi
===
Platform independent Python GUI library. In less than 100 Kbytes of source code, perfect for your diet.

<p align="center">
    <img src="https://raw.githubusercontent.com/rawpython/remi/development/remi/res/screenshot.png" title="Widgets overview">
</p>

Remi enables developers to create platform independent GUI with Python. The entire GUI is rendered in your browser. **No HTML** is required, Remi automatically translates your Python code into HTML. When your app starts, it starts a web server that will be accessible on your network.

A basic application appears like this:

```py
import remi.gui as gui
from remi import start, App

class MyApp(App):
    def __init__(self, *args):
        super(MyApp, self).__init__(*args)

    def main(self):
        container = gui.VBox(width=120, height=100)
        self.lbl = gui.Label('Hello world!')
        self.bt = gui.Button('Press me!')

        # setting the listener for the onclick event of the button
        self.bt.onclick.do(self.on_button_pressed)

        # appending a widget to another, the first argument is a string key
        container.append(self.lbl)
        container.append(self.bt)

        # returning the root widget
        return container

    # listener function
    def on_button_pressed(self, widget):
        self.lbl.set_text('Button pressed!')
        self.bt.set_text('Hi!')

# starts the web server
start(MyApp, port=8081)
```

In order to see the user interface, open your preferred browser and type "http://127.0.0.1:8081".
You can change the URL address by specific **kwargs at `start` function call. This will be discussed later.

Tested on Android, Linux, Windows.
Useful on Raspberry Pi for Python script development. It allows interacting with your Raspberry Pi remotely from your mobile device.


FAQ
===
- **Why another GUI lib?**
Kivy, PyQT, and PyGObject all require native code for the host operating system, which means installing or compiling large dependencies. Remi needs only a web browser to show your GUI.

- **Do I need to know HTML?**
NO, It is not required, you have to code only in Python.

- **Is it open source?**
For sure! Remi is released under the Apache License. See the ``LICENSE`` file for more details.

- **Do I need some kind of web server?**
No, it's included.


Brief tutorial
===
Import Remi library and some other useful stuff.

```py
import remi.gui as gui
from remi import start, App
```

Subclass the `App` class and declare a `main` function that will be the entry point of the application. Inside the main function you have to <code>return</code> the root widget.

```py
class MyApp(App):
    def __init__(self, *args):
        super(MyApp, self).__init__(*args)

    def main(self):
        lbl = gui.Label("Hello world!", width=100, height=30)

        # return of the root widget
        return lbl
```

Outside the main class, start the application by calling the function `start` and passing the name of the class you declared previously as the parameter:

```py
# starts the webserver
start(MyApp, port=8081)
```

Run the script. If it's all OK the GUI will be opened automatically in your browser, otherwise, you have to type in the address bar "http://127.0.0.1:8081".

You can customize optional parameters in the `start` call like:

```py
start(MyApp, address='127.0.0.1', port=8081, multiple_instance=False, enable_file_cache=True, update_interval=0.1, start_browser=True, dynamic_web_address=True)
```

Parameters:
- address: network interface IP
- port: listen port
- multiple_instance: boolean, if True multiple clients that connect to your script has different App instances (identified by unique cookie session identifier)
- enable_file_cache: boolean, if True enable resource caching
- update_interval: GUI update interval in seconds. If zero, the update happens at each change. If zero, the App.idle method is not called.
- start_browser: boolean that defines if the browser should be opened automatically at startup
- standalone: boolean, indicates where to run the application as a standard Desktop application with its own window. If False, the interface is shown in a browser webpage.

Additional Parameters:
- username: for a basic HTTP authentication
- password: for a basic HTTP authentication
- certfile: SSL certificate filename
- keyfile: SSL key file
- ssl_version: authentication version (i.e. ssl.PROTOCOL_TLSv1_2). If None disables SSL encryption
- dynamic_web_address: set it to `True` if the server is not aware of the IP address and the URL the user will be opening the app with.
If so, the JavaScript code will use hostname and port in the browser. This parameter is `False` by default.

All widgets constructors accept two standards**kwargs that are:
- width: can be expressed as int (and is interpreted as a pixel) or as str (and you can specify the measuring unit like '10%')
- height: can be expressed as int (and is interpreted as a pixel) or as str (and you can specify the measuring unit like '10%')


Events and callbacks
===
Widgets expose a set of events that happen during user interaction.
Such events are a convenient way to define the application behavior.
Each widget has its own callbacks, depending on the type of user interaction it allows.
The specific callbacks for the widgets will be illustrated later.

In order to register a function as an event listener you have to call a function like eventname.do (i.e. onclick.do) passing as parameters the callback that will manage the event.
Follows an example:

```py
import remi.gui as gui
from remi import start, App

class MyApp(App):
    def __init__(self, *args):
        super(MyApp, self).__init__(*args)

    def main(self):
        container = gui.VBox(width=120, height=100)
        self.lbl = gui.Label('Hello world!')
        self.bt = gui.Button('Press me!')

        # setting the listener for the onclick event of the button
        self.bt.onclick.do(self.on_button_pressed)

        # appending a widget to another, the first argument is a string key
        container.append(self.lbl)
        container.append(self.bt)

        # returning the root widget
        return container

    # listener function
    def on_button_pressed(self, widget):
        self.lbl.set_text('Button pressed!')
        self.bt.set_text('Hi!')

# starts the web server
start(MyApp)
```

In the shown example *self.bt.onclick.do(self.on_button_pressed)* registers the self's *on_button_pressed* function as a listener for the event *onclick* exposed by the Button widget.
Simple, easy.

Listener's callbacks will receive the emitter's instance firstly, then all other parameters provided by the specific event.


Besides the standard event registration (as aforementioned), it is possible to pass user parameters to listener functions. This can be achieves appending parameters to the *do* function call.

```py
import remi.gui as gui
from remi import start, App

class MyApp(App):
    def __init__(self, *args):
        super(MyApp, self).__init__(*args)

    def main(self):
        container = gui.VBox(width=120, height=100)
        self.lbl = gui.Label('Hello world!')
        self.bt = gui.Button('Hello name!')
        self.bt2 = gui.Button('Hello name surname!')

        # setting the listener for the onclick event of the buttons
        self.bt.onclick.do(self.on_button_pressed, "Name")
        self.bt2.onclick.do(self.on_button_pressed, "Name", "Surname")

        # appending a widget to another
        container.append(self.lbl)
        container.append(self.bt)
        container.append(self.bt2)

        # returning the root widget
        return container

    # listener function
    def on_button_pressed(self, widget, name='', surname=''):
        self.lbl.set_text('Button pressed!')
        widget.set_text('Hello ' + name + ' ' + surname)

# starts the web server
start(MyApp)
```

This allows great flexibility, getting different behaviors with the same event listener definition.


HTML Attribute accessibility
===
Sometimes it is required to access Widget's HTML representation in order to manipulate HTML attributes.
The library allows accessing this information easily.

A simple example: It is the case where you would like to add a hover text to a widget. This can be achieved by the *title* attribute of an HTML tag.
In order to do this:

```py
    widget_instance.attributes['title'] = 'Your title content'
```

A special case of HTML attribute is the *style*.
The style attributes can be altered in this way:

```py
    widget_instance.style['color'] = 'red'
```

The assignment of a new attribute automatically creates it.

For a reference list of HTML attributes, you can refer to https://www.w3schools.com/tags/ref_attributes.asp

For a reference list of style attributes, you can refer to https://www.w3schools.com/cssref/default.asp

Take care about internally used attributes. These are:
- **class**: It is used to store the Widget class name for styling purpose
- **id**: It is used to store the instance id of the widget for callback management


Remote access
===
If you are using your REMI app remotely, with a DNS and behind a firewall, you can specify special parameters in the `start` call:
- **port**: HTTP server port. Don't forget to NAT this port on your router;
- **dynamic_web_address**: set to `True` if the JavaScript code should use the actual URL's host and port for connecting back to the app, instead of provided IP address. This parameter is `False` by default.

```py
start(MyApp, address='0.0.0.0', port=8081, dynamic_web_address=True)
```


Standalone Execution
===
I suggest using the browser as a standard interface window.

However, you can avoid using the browser.
This can be simply obtained joining REMI and [PyWebView](https://github.com/r0x0r/pywebview).
Here is an example about this [standalone_app.py](https://github.com/rawpython/remi/blob/development/examples/standalone_app.py).

**Be aware that PyWebView uses qt, gtk and so on to create the window. An outdated version of these libraries can cause UI problems. If you experience UI issues, update these libraries, or better avoid standalone execution.**


Authentication
===
In order to limit remote access to your interface, you can define a username and password. It consists of a simple authentication process.
Just define the parameters **username** and **password** in the start call:
```py
start(MyApp, username='myusername', password='mypassword')
```


Styling
===
In order to define a new style for your app, you have to do the following.
Create a *res* folder and pass it to your App class constructor:
```python
class MyApp(App):
    def __init__(self, *args):
        res_path = os.path.join(os.path.dirname(__file__), 'res')
        super(MyApp, self).__init__(*args, static_file_path={'res':res_path})
```

Copy the standard style.css file from the remi folder and paste it inside your *res* folder. Edit it in order to customize.
This way the standard *style.css* file gets overridden by the one you created.


Compatibility
===
Remi is made to be compatible from Python2.7 to Python3.X. Please notify compatibility issues.


Security
===
Remi should be intended as a standard desktop GUI framework.
The library itself doesn't implement security strategies, and so it is advised to not expose its access to unsafe public networks.

When loading data from external sources, consider protecting the application from potential javascript injection before displaying the content directly.


Projects using Remi
===
[PySimpleGUI](https://github.com/PySimpleGUI/PySimpleGUI): Launched in 2018 Actively developed and supported. Supports tkinter, Qt, WxPython, Remi (in browser). Create custom layout GUI's simply. Python 2.7 & 3 Support. 100+ Demo programs & Cookbook for rapid start. Extensive documentation.

[App Template For REMI](https://github.com/cheak1974/remi-app-template): A really well written template for multiview applications.

[Web based dynamic reconfigure for ROS robots](https://github.com/awesomebytes/web_dyn_reconf)

[razmq](https://github.com/MrYsLab/razmq)

[Espresso-ARM](http://hallee.github.io/espresso-arm/)

[PiPresents](https://github.com/KenT2/pipresents-gapless)

[The Python Banyan Framework](https://github.com/MrYsLab/python_banyan)

[LightShowPi show manager](https://github.com/Chrispizzi75/ShowManager)

[rElectrum](https://github.com/emanuelelaface/rElectrum): A powerful promising Electrum wallet manager for safe transactions.

Other Implementations
===
Listed here are other implementations of this library:
- [**cremi**](https://github.com/cyberpro4/cremi): (WIP) developed for your C++ projects by [Claudio Cannatà](https://github.com/cyberpro4).

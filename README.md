# Roky
Roky is a Windows console client for the Roku Debugger that provides line-editing, command history, and support for Unicode debugger output, unlike most Windows Telnet clients.

Roky is a Python program requiring Python version 3.5 or later. It might be possible to port the code to run on a Mac, or to use an earlier version of Python, but I'll leave that up to others.

Roky has been tested to run under the Windows cmd.exe, PowerShell, and MinGW command interpreters.

## Installation
Download `roky.py` from https://github.com/belltown/roky/blob/master/roky.py [right-click on the `Raw` button, then click `Save Link As...` or `Save target as...`, depending on your browser].

Download and run the Python Windows installer (version 3.5 or higher) from: https://www.python.org/downloads/. This should install the Python Launcher, `py`.

If you only have one version of Python installed, you can run roky using:
```
py roky.py 192.168.0.12
```
If you have both versions 2 and 3 of Python installed, you may need to type:
```
py -3 roky.py 192.168.0.12
```
The Roku IP address has a default value in the code that you can easily change to avoid having to type the IP address every time.

For quick launching, pin a Command Prompt shortcut to your Windows taskbar, right-click on it, then right-click again on the context-menu Command Prompt icon, select Properties, then change Target to: `C:\Windows\System32\cmd.exe /k "py -3 C:\Programs\roky.py 192.168.0.12"`, substituting your own Roku IP address and roky.py location. You can also change the Command Prompt font, colors, screen buffer size, etc.

## Help

````
    py roky.py -h
````

````
usage: roky.py [-h] [-f font-height] [-o output-file] [host] [port]

roky -- the Roku Debugger wrapper

positional arguments:
  host            Roku's IP address (default 192.168.0.6)
  port            Roku's debugging port (default 8085)

optional arguments:
  -h, --help      show this help message and exit
  -f font-height  Consolas font height in pixels
  -o output-file  log debug output to file
````

Documention on [GitHub](https://github.com/belltown/roky/blob/master/README.md) and at http://belltown-roku.appspot.com/roky.html

## Line-Editing
The small console window supports these line-editing keys:
- Page Up: first history item
- Page Down: last history item
- Home: start of line
- End: end of line
- Up arrow: previous history item
- Down arrow: next history item
- Left arrow: cursor left
- Right arrow: cursor right
- Backspace: delete character before cursor
- Delete: delete character at cursor
- Enter: send line to Roku Debugger
- Insert: toggle between insert and over-write

All Roku Debugger commands may be used. Ctrl/C breaks into the debugger.

Type `quit` in the small command window to exit.

## Unicode Support
The debugger output window has full Unicode support. Ideally, set your Windows console font to `Consolas`, which can display the first 1300 Unicode characters: click the console icon (top-left corner of the console window), select `Properties>Font`, then set your Font and Size. Alternatively, use the roky `-f` command-line option, e.g. `-f 20`, which will automatically use the Consolas font with the specified pixel height.

Unicode characters above 1300 are rendered as one or two `\uhhhh` sequences. Note that some of the ASCII control characters are escaped as `\xhh`. Other ASCII control characters come from the Roku as `space` or `?`.

## Limitations
Due to the way the Windows console and Python readline functions work, roky requires two independent console windows: one for entering debugger *commands*, and one for viewing debugger *output* only. However, roky will create the second command window for you, and you can move and resize the windows. For example, you can move the main window off to the (right) side, so you still have a partial view of your BrightScript code, and put the smaller command window overlaying, or above, the main window.

![Image of roky](http://belltown-roku.appspot.com/roky-1200.png)

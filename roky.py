#
# roky.py version 0.0, by belltown, January, 2016.
#
# Roku Debugger client.
#
# This script acts as a client to interface with the Roku Debugger.
# Two console windows are used: a small one is created for user input (debug commands); and the current console used for debugger output.
# The small console window employs Python's readline functionality, including history, etc, to assist in a Roku debug session.
#
# Tested on Windows only. Requires Python 3.5 or higher.
#

# Make sure Python 2 users don't get a syntax error when we print an invalid version warning
from __future__ import print_function


rokyEpilog = r'''

NOTE: roky must be run on Windows using Python version 3.5 or later.
It has been tested on the Windows cme.exe console, Powershell, and MinGW.

Two independent console windows are used:
Your main console window is used for Roku Debugger output;
A small window is created for entering Roku Debugger commands.
You may wish to position the windows so they don't overlap.

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

All Roku Debugger commands may be used. Ctrl/C breaks into the Debugger.

Type 'quit' in the small command window to exit.

The debugger output window has full Unicode support.
Ideally, set your Windows console font to "Consolas",
which can display the first 1300 Unicode characters:
click the console icon (top-left corner of console window),
select Properties>Font, then set your Font and Size.
Alternatively, use the roky -f command-line option, e.g. -f 20,
which will use the Consolas font with the specified pixel height.

Unicode characters above 1300 are rendered as one or two \uhhhh sequences.
Note that some of the ASCII control characters are escaped as \xhh.
Other ASCII control characters from the Roku are output as space or "?".

You can also set your console window's buffer size, e.g:
Properties>Layout>Screen Buffer Size>Height set to 9999

Documentation and source code at https://github.com/belltown/roky

'''

#
# This program is implemented as two separate processes: a main process, and the child process it spawns.
#
# The child process creates a small console window for user-input of BrightScript Debugger commands.
# The child process reads user input using input(), which internally calls Python's readline().
# Each line is sent to the main process via a blocking TCP stream socket.
# When the write to the main process completes, the next line can be read, until the user enters the 'quit' command.
#
# The main process does everything else. It starts three threads: one thread receives data from the Roku;
# another thread sends data to the Roku; and another thread reads user data from the console, sent via TCP socket from the child process.
# There are two general data flows:
# (1) User input (child proc) => console thread (main proc) => Roku writer thread AND console output (main window).
# (2) Roku => Roku reader thread (main proc) => console output (main window) AND log file.
#
# The reason for using two consoles is that Windows won't allow a process to be reading and writing to the console simultaneously.
# Since readline() is a blocking operation with no provisions for a timeout, there would be no way to display
# Roku Debugger output on the console until the user presses the enter key to complete the current read operation.
# It's possible to read the console a character at a time using the msvcrt module, but then you'd lose the readline functionality.
#
# Ctrl/C, which causes the BrightScript debugger to break execution, is handled by using a custom SIGINT handler.
# All this handler does is to return, thus preventing the KeyboardInterrupt event from being raised.
# In turn, an EOFError event is raised in the child process's console window which can easily be handled.
# The child process sends a 'break' command to the main process's console input thread,
# which sends an ETX character to the Roku to break it.
#

ROKU = '192.168.0.6'    # May be overridden using the 1st positional command-line argument
PORT = 8085             # May be overridden using the 2nd positional command-line argument

import sys

# Only support Python versions 3.5 and higher -- do this check before we start using any Python 3 imports or code
if sys.version_info.major < 3 or (sys.version_info.major == 3 and sys.version_info.minor < 5):
    print("This program requires Python version 3.5 or later.\nYour Python version is: " + sys.version)
    sys.exit()

import os
import io
import re
import time
import queue
import ctypes
import ctypes.wintypes
import signal
import socket
import argparse
import threading
import subprocess

# Printable ASCII characters will be printed as-is (including TAB, CR and LF). The rest will be hex backslash-escaped.
# Unfortunately, the Roku won't output several of the ASCII control codes, outputting question marks or spaces instead.
printable = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0
    ]

# Use a lock to control access to the print function by all threads
printLock = threading.Lock()

def tPrint(s):
    '''Thread-safe print function.'''

    acquiredLock = printLock.acquire(timeout=5)
    print(s)
    if acquiredLock: printLock.release()

def tPrintFlush(s):
    '''Thread-safe print function, flushing the print output.'''

    acquiredLock = printLock.acquire(timeout=5)
    print(s, end='', flush=True)
    if acquiredLock: printLock.release()


################ Windows API Code ################

# Win32 API
Win32 = ctypes.windll.kernel32

# Win32 Data Types and Constants
BOOL                    = ctypes.wintypes.BOOL
UINT                    = ctypes.wintypes.UINT
ULONG                   = ctypes.wintypes.ULONG
WCHAR                   = ctypes.wintypes.WCHAR
HANDLE                  = ctypes.wintypes.HANDLE
SHORT                   = ctypes.wintypes.SHORT
DWORD                   = ctypes.wintypes.DWORD
NULL                    = None
TRUE                    = BOOL(1)
FALSE                   = BOOL(0)
STD_OUTPUT_HANDLE       = DWORD(-11)
INVALID_HANDLE_VALUE    = -1
FILE_TYPE_CHAR          = 0x0002
LF_FACESIZE             = 32

# Win32 API functions
GetFileType             = Win32.GetFileType
GetStdHandle            = Win32.GetStdHandle
WriteConsoleW           = Win32.WriteConsoleW
GetConsoleMode          = Win32.GetConsoleMode
GetCurrentConsoleFontEx = Win32.GetCurrentConsoleFontEx
SetCurrentConsoleFontEx = Win32.SetCurrentConsoleFontEx

class COORD(ctypes.Structure):
    '''Win32 COORD Struct.'''

    _fields_ = [('X', SHORT), ('Y', SHORT)]

class CONSOLE_FONT_INFOEX(ctypes.Structure):
    '''Win32 Font CONSOLE_FONT_INFOEX Struct.'''

    _fields_ = [('cbSize',      ULONG),
                ('nFont',       DWORD),
                ('dwFontSize',  COORD),
                ('FontFamily',  UINT),
                ('FontWeight',  UINT),
                ('FaceName',    WCHAR * LF_FACESIZE)]

    def __str__(self):
        return 'Font({0.nFont}, {0.dwFontSize.X}, {0.dwFontSize.Y}, {0.FontFamily}, {0.FontWeight}, "{0.FaceName}")'.format(self)

SIZEOF_CONSOLE_FONT_INFOEX = ctypes.sizeof(CONSOLE_FONT_INFOEX)

def Font(number, size, family, weight, name):
    '''Win32 Font CONSOLE_FONT_INFOEX Struct setter.'''

    struct = CONSOLE_FONT_INFOEX()
    struct.cbSize       = SIZEOF_CONSOLE_FONT_INFOEX    # ULONG - The size of this structure, in bytes.
    struct.nFont        = number                        # DWORD - The index of the font in the system's console font table.
    struct.dwFontSize   = size                          # COORD - the size of each character in the font, in logical units.
    struct.FontFamily   = family                        # UINT - The font pitch and family.
    struct.FontWeight   = weight                        # UINT - The font weight. A range from 100 to 1000, (400 = normal, 700 = bold).
    struct.FaceName     = name                          # WCHAR[32] - The name of the typeface.
    return struct

def getFont():
    '''Get the console terminal's current font.'''

    try:
        struct = CONSOLE_FONT_INFOEX()
        struct.cbSize = SIZEOF_CONSOLE_FONT_INFOEX
        if not GetCurrentConsoleFontEx(GetStdHandle(STD_OUTPUT_HANDLE), FALSE, ctypes.pointer(struct)):
            return None
        else:
            return struct
    except Exception as e:
        # No harm done if we can't get the current font
        print("\n{}\n\nroky: Unable to get the current font\n".format(e))
        return None

def setFont(font, fontSize=None):
    '''Set the console terminal's font.'''

    try:
        # If a fontSize is specified, use it to set the size of the new font
        if fontSize:
            newFont = Font(font.nFont, COORD(0, fontSize), font.FontFamily, font.FontWeight, font.FaceName)
        else:
            newFont = font
        return SetCurrentConsoleFontEx(GetStdHandle(STD_OUTPUT_HANDLE), FALSE, ctypes.pointer(newFont))
    except Exception as e:
        # If we can't set the font, proceed without UTF-8 font support
        print("\n{}\n\nroky: Unable to change the font\n".format(e))
        return False

# Supported console terminal fonts -- should have Consolas on all modern Windows OS's.
# Other fonts can be installed, that may have better UTF-8 support, using a registry hack.
CONSOLAS    = Font(0, COORD(0, 20), 54, 400, "Consolas")        # http://www.fileformat.info/info/unicode/font/consolas/list.htm
LUCIDA      = Font(0, COORD(0, 18), 54, 400, "Lucida Console")  # http://www.fileformat.info/info/unicode/font/lucida_console/list.htm

# The font to use from the above list
FONT = CONSOLAS


class Console:
    '''Use native Windows API to write UTF-16 characters to the console if possible.'''

    def __init__(self):
        '''Determine whether stdout serves an actual Windows Console.'''

        self.console = False
        self.hStdOut = None

        # Check whether we are writing to an actual Windows console, or something else (e.g. MinGW)
        self.hStdOut = GetStdHandle(STD_OUTPUT_HANDLE)

        # Check stdout handle is valid
        if self.hStdOut and self.hStdOut != INVALID_HANDLE_VALUE:

            # Check stdout has a file type of FILE_TYPE_CHAR
            fileType = GetFileType(self.hStdOut)

            # The File Type will be FILE_TYPE_CHAR for a Windows Console.
            # For non-Windows consoles, e.g. MinGw, it might be FILE_TYPE_PIPE.
            if fileType == FILE_TYPE_CHAR:

                # Check if stdout is an actual console; if so, then any call to GetConsoleMode should succeed
                if GetConsoleMode(self.hStdOut, ctypes.byref(DWORD())) != 0:
                    self.console = True

    def write(self, text):
        '''Write to the Windows Console using the Win32 API, rather than print, if possible.'''

        # The Windows Console is known to have buggy handling of UTF-8 characters, as verified during my testing.
        # By using the native Windows API, UTF-16LE characters will be output instead, which the console has no problem with,
        # assuming the font in use supports the necessary code points.
        if self.console:
            nWritten = DWORD(0)
            bRet = WriteConsoleW(self.hStdOut, text, len(text), ctypes.byref(nWritten), NULL)
            if bRet == 0:
                # Error -- fallback to using print
                tPrintFlush(text)
        else:
            tPrintFlush(text)

########### End Windows API code ############


class LogWriter():
    '''Logging functions.'''

    def __init__(self, logFile):
        '''Open the specified file for output logging.'''

        self.logFile = logFile
        self.logFd = None

        try:
            if self.logFile:
                self.logFd = open(self.logFile, 'wb')
        except Exception as e:
            print("{}\n\nroky: Unable to open log file {}\n".format(e, self.logFile))
            self.logFd = None

    def write(self, bytesIn):
        '''Write the data to the log file if it is open.'''

        if self.logFd:
            try:
                self.logFd.write(bytesIn)
                self.logFd.flush()
            except Exception as e:
                print("\n{}\n\nroky: Unable to write to log file: {}\n".format(e, self.logFile))
                # Make sure we don't keep trying to write to the log file if something went wrong
                self.logFd = None

    def close(self):
        '''Close the log file.'''

        if self.logFd:
            self.logFd.close()
            self.logFd = None


def consoleFormat(bytesIn):
    '''Take an aritrary byte string that 'should' contain valid UTF-8, formatting it for display on the Windows console.'''

    # Return a character string that can be printed on the user's console window, assuming a Consolas font is being used
    consoleChars = ''

    # Do our best to output valid UTF-8 code points
    try:
        # Decode the bytes received from the Roku.
        # Use 'backslashreplace' for invalid characters so the user can see the character values of the invalid data.
        decodedChars = bytesIn.decode(errors='backslashreplace')

        # Print all code-points "as is" up to 0x513, the highest value handled by the Consolas font.
        # If the code point corresponds to a Unicode surrogate pair, then print the pair as a UTF-16 hex pair: "\uhhhh\uhhhh".
        # If the code point is above the max allowable Unicode code point (shouldn't happen), print a Unicode Replacement Character.
        for c in decodedChars:

            # Get the numeric value of the character (Unicode code point number)
            cp = ord(c)

            # Code points 0-x7F (0-127) are supported by the Windows console
            if cp < 0x80:
                # Print the character "as is" if it's a printable character, otherwise backslash-escape as \xhh
                # In practice, most of the non-printable ASCII characters are output as "?" or space by Roku.
                if printable [cp] == 1:
                    consoleChars += c
                else:
                    consoleChars += "\\x{:02x}".format(cp)

            # Code points from x0080 to x0513 (128-1299) are output correctly by the Roku and can be displayed by the Windows console,
            # at least when using the Consolas font, which supports the first 1300 Unicode characters.
            elif cp <= 0x0513:
                consoleChars += c

            # Code points up to xFFFF are in the Unicode Basic Multilingual Plane, occupying 16 bits
            elif cp < 0x10000:
                # Print the unicode-escaped value of the code point: "\uhhhh"
                consoleChars += "\\u{:04x}".format(cp)

            # Valid Unicode code points from x10000 to x10FFFF are represented by a UTF-16 surrogate-pair
            elif cp < 0x110000:
                # If you don't understand any of the following, take a look at: https://www.ietf.org/rfc/rfc2781.txt sec 2.1
                bt20 = cp - 0x10000
                hi10 = (((bt20 & 0xFFC00) >> 10) & 0x3FF) + 0xD800
                lo10 = (bt20 & 0x3FF) + 0xDC00
                consoleChars += "\\u{:04x}\\u{:04x}".format(hi10, lo10)

            # Code points above 10FFFF are invalid - We shouldn't get here if decode() works correctly
            else:
                consoleChars += '\ufffd'   # Just use the Unicode Replacement Character

    except:
        # Shouldn't get here, as decode() is supposed to replace invalid Unicode, not throw an exception
        consoleChars = '**** Unicode Decode Error ****'
    return consoleChars


def rokuReaderThread(rokuSocket, console, quitQ, log):
    '''Within the main process, receive debugger output from the Roku, writing it to the console and the log file.'''

    quitMsg = ''

    # Keep track of trailing UTF-8 characters that are split across socket receives.
    # If we encounter a split UTF-8 byte sequence at the end, strip if off, adding it back when the next packet is read.
    # This is not implemented very efficiently. However, it doesn't matter; this is a rare occurrence requiring
    # a large data packet read from the Roku (4096 bytes), ending with a UTF-8 multi-byte character sequence,
    # which happens to have been split at the end of the packet.
    trail = b''

    # This thread runs as a daemon thread that will be terminated when the program ends
    while True:
        # Read the data from the Roku using this (blocking) socket
        try:
            # Raw bytes (hopefully valid UTF-8) come in from the Roku
            bytesIn = rokuSocket.recv(4096)

            # Include any trailing UTF-8 characters that begun in the previous socket recv
            if trail:
                bytesIn = trail + bytesIn
                trail = b''      # Must reset so we can detect trailing split UTF-8 at end of current packet
        except Exception as e:
            quitMsg = "\n{}\n\nroky: Roku reader thread unable to receive data from Roku socket".format(e)
            break

        if bytesIn:
            # Log the data without decoding the input bytes.
            # The log file was opened in binary mode, so it doesn't care what format the Roku data is.
            log.write(bytesIn)

            # Count the number of UTF-8 continuation bytes at the end of the input byte stream
            i = len(bytesIn)
            while (i > 0) and ((bytesIn[i - 1] >> 6) == 0b10):  # All UTF-8 continuation bytes are in the form: 10xxxxxx
                i -= 1
            nCont = len(bytesIn) - i

            # Get the last non-continuation byte (if i = 0 then bytesIn starts with a continuation
            # character, which should not happen, but if it does just treat the whole packet as a trailing sequence)
            expectedCont = 0
            if i > 0:
                leadByte = bytesIn[i - 1]
                # Determine the expected number of continuation bytes based on the lead byte's bit pattern
                if (leadByte >> 5) == 0b110:
                    expectedCont = 1
                elif (leadByte >> 4) == 0b1110:
                    expectedCont = 2
                elif (leadByte >> 3) == 0b11110:
                    expectedCont = 3
                else:
                    pass
                # Check if this packet ends in a partial UTF-8 byte sequence
                if nCont < expectedCont:
                    # Extract the partial continuation sequence
                    trail = bytesIn[i - 1:]
                    # Strip off the partial continuation sequence
                    bytesIn = bytesIn[:i - 1]
            else:
                trail = bytesIn
                bytesIn = b''

            # For example, roky can be used on port 8080 to run genkey, which outputs a single character at a time.
            try:
                # Decode the Roku bytes and write to the console using the native Windows API, if possible
                console.write (consoleFormat (bytesIn))

            # Hopefully, the user's console can handle the UTF-8 data to be displayed.
            # If not, a UnicodeEncodeError may be raised by the console charmap handler.
            # Attempt to continue if we get a Unicode exception.
            except UnicodeEncodeError as e:
                tPrint("\n{}\n\nroky: Roku reader thread unable to print UTF-8 data to console window\n".format(e))
            except Exception as e:
                quitMsg = "\n{}\n\nroky: Roku reader thread unable to write to windows console".format(e)
                break

    # If we get this far, something went wrong, so signal the main thread that we are dying
    # Note - it's better to have the main thread print the error, since this thread is a daemon, and
    # there may be some contention if this thread tries to print while the program is terminating, due to a Python bug.
    quitQ.put(quitMsg)


def rokuWriterThread(rokuSocket, rokuWriterQ, quitQ, log):
    '''Within the main process, send queued data to the Roku.'''

    quitMsg = ''

    # This thread runs as a daemon thread that will be terminated when the program ends
    while True:
        # Get data from the Roku write queue (blocking)
        data = rokuWriterQ.get()

        # Send data to the Roku (blocking)
        try:
            while data:
                # Send as much of our data as we can, noting how much was actually sent
                bytesSent = rokuSocket.send(data)
                # Because of the way sockets work, we may not be able to send the whole packet at once
                data = data [bytesSent:]
        except Exception as e:
            quitMsg = "\n{}\n\nroky: Roku writer thread unable to write data to Roku socket".format(e)
            break

    # If we get this far, something went wrong, so signal the main thread that we are dying
    # Note - it's better to have the main thread print the error, since this thread is a daemon, and
    # there may be some contention if this thread tries to print while the program is terminating, due to a Python bug.
    quitQ.put(quitMsg)


def consoleThread(sock, rokuWriterQ, quitQ, log):
    ''' Within the main process, receive user's console input via a TCP socket connection with the child process.'''

    reLines = re.compile(r'[^\r]*\r')   # findall() to get each line of user input, lines being terminated by \r characters
    reTrail = re.compile(r'[^\r]*\Z')   # search() to get any trailing user input data past the last \r character

    quitMsg = ''

    # Accept a socket connection from the client
    try:
        clientSock, addr = sock.accept()
    except Exception as e:
        tPrint("\n{}\n\nroky: Console thread unable to accept client socket connection".format(e))
        return

    # Process all data sent from the child process (user console input), writing data to the Roku
    try:
        charBuf = ''
        while True:
            # We only need a small receive buffer, since the user's debug commands tend to be very short
            bytesIn = clientSock.recv(256)

            # Since a blocking socket is used, when the client end of the socket is closed, 'None' will be returned when the socket closes
            if not bytesIn:
                quitMsg = "\n\nroky: Console thread client socket data finished"
                break

            # Log the data
            log.write(bytesIn + b'\n')

            # Convert client's user input bytes into a character string
            charBuf += bytesIn.decode(errors='replace')

            # Terminate the connection if the client issues a 'quit' command
            if 'quit\r' in charBuf:
                quitMsg = "\n\nroky: Console thread terminating"
                break

            # If a 'break' command is received, send a ctrl/c [ETX] to the Roku, as a signal to break into the debugger
            if 'break\r' in charBuf:
                tPrint("roky: Breaking into debugger")
                charBuf = ''
                rokuWriterQ.put_nowait(b'\x03')

            # Scan the user input data for \r-terminated lines
            else:
                # Loop for each line terminated in a single \r character
                for line in reLines.findall(charBuf):

                    # Output the line to the console, stripping off the line terminator
                    tPrint(line.rstrip('\r'))

                    # Write the line to the Roku device, ensuring it ends in \r (already in line) and \n (added)
                    rokuWriterQ.put_nowait(line.encode() + b'\n')

                # Don't write the trailing data past the last \r; instead, keep it buffered until the end of the line is received
                trail = reTrail.search(charBuf)
                charBuf = trail.group()

    except Exception as e:
        quitMsg = "\n\n{}\n\nroky: Console thread socket error".format(e)
    finally:
        clientSock.close()
        # Signal the main thread that we are terminating
        quitQ.put(quitMsg)


def getArgs():
    '''Parse command-line arguments.'''

    parser = argparse.ArgumentParser(description="roky -- the Roku Debugger wrapper", epilog=rokyEpilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)      # [Python 3.2]
    parser.add_argument('-f', metavar='font-height', help="Consolas font height in pixels", type=int,
                        choices=[5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 24, 28, 36, 72])
    parser.add_argument('-o', metavar='output-file', help="log debug output to file")
    parser.add_argument('host', help="Roku's IP address (default " + ROKU + ")", nargs='?', default=ROKU)
    parser.add_argument('port', help="Roku's debugging port (default " + str(PORT) + ")", nargs='?', default=PORT, type=int)
    return parser.parse_args()


def parentMain():
    '''Handle user's console input piped in to stdin, and Roku Debugger input and output'''

    # Parse command-line arguments
    args = getArgs()

    # Create a Console object used to write to the Windows Console using the native Windows API
    console = Console()

    # NOTE: To get UTF-8 characters displayed correctly on the Windows console, requires three things:
    # 1. Tell the Windows console to use code page 65001 (a Windows 'UTF-8' code page), rather that its default code page 437.
    # 2. Set the font used by the Windows console to a font that supports (some) UTF-8 characters, rather than its default raster font.
    # 3. Tell Python that our output device uses UTF-8 encoding, rather than code page 437, which is determined at program startup.

    # 1. Set the Windows console's code page to 65001 (Windows' version of UTF-8), so we can display UTF-8 chars on the console.
    # This isn't really necessary any more as long as it is possible to use the native Windows API to write UTF-16 to the console.
    # However, leave it in, in case for some reason we can't write native UTF-16 to the Windows Console
    # [Windows-only]
    subprocess.run('chcp', shell=True)          # before    [Python 3.5]
    print("Attempting to change code page")
    subprocess.run('chcp 65001', shell=True)    # after     [Python 3.5]

    # 2, The default Windows console font is 'Raster Fonts', which does not have much UTF-8 support.
    # Both Consolas and Lucida Console have some UTF-8 support. Supposedly Consolas has better Unicode support.
    # [Windows-only]
    oldFont = None
    if args.f:
        oldFont = getFont()
        if setFont(FONT, args.f):
            print("Changing fonts: Old font: {}. New font: {}".format(oldFont, getFont()))

    # 3. Make sure the console uses the utf-8 code page.
    # By default, some Windows consoles uses cp437, a code page from the original IBM PC days, which does not support UTF-8.
    # Use detach() to remove the existing text encoding layer from sys.stdout, then replace with the new encoding layer.
    # Note: sys.stdout.encoding is the encoding that Python thinks the console uses to interpret text. We must change that.
    # A better way to do this is to set PYTHONIOENCODING=UTF-8 in the console before running Python,
    # but it's not really feasible to expect all our users to do that before running this script.
    # Note that if the native Windows API is used to write UTF-16 to the Windows Console, this is no longer necessary.
    # However, it is still necessary for non-Windows consoles, e.g. MinGW/Git Bash, etc.
    print('Default console encoding: {}'.format(sys.stdout.encoding))
    sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8', errors='backslashreplace', line_buffering=True)   # Python 3.1
    print('Console encoding changed to: {}'.format(sys.stdout.encoding))

    # Warn the user if not running a Windows OS, but continue anyway
    if os.name != 'nt':
        print("\nWARNING - This program has only been tested on Windows operating systems!\n")

    # Create and open a log file if the -o <logFile> command-line option was specified
    logWriter = LogWriter(args.o)

    # Create a queue for data to be sent to the Roku by the Roku writer thread
    rokuWriterQ = queue.Queue()

    # Create a queue for the worker threads to notify the main thread when they are quitting.
    # Terminate the program if any thread quits.
    quitQ = queue.Queue()

    # Create a streaming, blocking TCP socket to receive user console data from the child process
    try:
        sock = socket.socket()
    except Exception as e:
        print("\n{}\n\nroky: Unable to create server socket".format(e))
        return

    # Associate the server socket with a random TCP port.
    # Note - I haven't seen any documentation indicating that a bind port parameter of zero results in a random port assignment;
    # however, it does work (at least on Windows).
    try:
        sock.bind(('localhost', 0))

        # Allow the server socket to listen for incoming client connections
        sock.listen(1)

        # Find out which TCP port has been assigned to the server socket
        addr, port = sock.getsockname()

        print("Server bound to {}:{}".format(addr, port))
    except Exception as e:
        print("\n{}\n\nroky: Unable to bind to server socket".format(e))
        sock.close()
        return

    # Spawn a child process to receive console input, which it will send to the main process over a streaming TCP socket
    # [May need modification for non-Windows OS]
    scriptPath = '"' + os.path.abspath(os.path.dirname(sys.argv[0])) + '\\' + os.path.basename(sys.argv[0]) + '"'
    pythonPath = sys.executable or 'python'
    scriptArgs = ' --parent-port ' + str(port)
    spawn = pythonPath + ' ' + scriptPath + scriptArgs
    print(spawn)
    try:
        proc = subprocess.Popen(spawn, universal_newlines=False, creationflags=subprocess.CREATE_NEW_CONSOLE)
    except Exception as e:
        print("\n{}\n\nroky: Unable to spawn child process: \n      {}".format(e, spawn))
        sock.close()
        return

    # Create the streaming, blocking TCP socket for communications with the Roku
    print("Attempting to establish connection with {}:{}".format(args.host, args.port))
    try:
        rokuSocket = socket.create_connection((args.host, args.port))
    except Exception as e:
        print("\n{}\n\nroky: Unable to connect to Roku socket at {}:{}".format(e, args.host, args.port))
        sock.close()
        return

    print("Connected to {}:{}\n".format(args.host, args.port))

    # Start a thread to receive console input data from the user.
    # This thread can start first. It doesn't rely on the other threads being available yet,
    # as it writes to a queue.
    try:
        threading.Thread(target=consoleThread, args=(sock, rokuWriterQ, quitQ, logWriter), daemon=True).start()
    except Exception as e:
        tPrint("\n{}\n\nroky: Unable to start console reader thread".format(e))
        sock.close()
        return

    # Start a thread to send data to the Roku
    try:
        threading.Thread(target=rokuWriterThread, args=(rokuSocket, rokuWriterQ, quitQ, logWriter), daemon=True).start()
    except Exception as e:
        tPrint("\n{}\n\nroky: Unable to start Roku writer thread".format(e))
        sock.close()
        return

    # Start a thread to receive data from the Roku.
    # Start this thread last because it writes to stdout, which is not thread-safe.
    # If anything goes wrong when starting up either of the other two threads, we might get an
    # exception if the failed thread tries to print to stdout at the same time as the rokuReader thread is printing to stdout.
    # After the rokuReader thread starts, there should be no other threads writing to stdout until the program terminates.
    try:
        threading.Thread(target=rokuReaderThread, args=(rokuSocket, console, quitQ, logWriter), daemon=True).start()
    except Exception as e:
        tPrint("\n{}\n\nroky: Unable to start Roku reader thread".format(e))
        sock.close()
        return

    # Wait for any of the worker threads to terminate
    quitMsg = quitQ.get()

    # Print any exception messages in the main thread rather than in the daemons, in case they
    # occur during program shutdown, which could result in problems.
    if quitMsg:
        tPrint(quitMsg)

    # Socket should be closed when garbage collection occurs, but close it explicitly anyway.
    # Only close the socket to our child process, not the Roku socket, otherwise when quitting,
    # the rokuReader thread will get an exception when trying to read from the socket.
    # If we try to print the ensuing exception message from the daemon thread while shutting down,
    # we could run into problems.
    try:
        sock.close()
    except:
        pass

    # Close the log file if it was opened
    logWriter.close()

    # Restore the old font if it was changed
    # [Windows-only]
    if args.f and oldFont:
        setFont(oldFont)


def childMain(port):
    '''Read user input from the console, passing to the parent process using a TCP socket.'''

    # Set a dummy SIGINT handler to ignore user input of ctrl/c
    # [May need modification for non-Windows OS]
    signal.signal(signal.SIGINT, lambda signum, frame: {})

    # Resize the console window (has no effect on the size of the history buffer) [Windows only]
    # [Windows-only]
    try:
        subprocess.run('mode con lines=10', shell=True)     # [Python 3.5]
        subprocess.run('mode con cols=80', shell=True)      # [Python 3.5]
    except Exception as e:
        print("roky: Unable to resize console window\n{}\nContinuing . . .\n".format(e))

    print("roky: Initiating debug connection")

    # Create a blocking TCP stream socket for communicating with the parent process
    try:
        sock = socket.socket()
    except Exception as e:
        # Shouldn't be any legitimate reason why we can't create a socket, but check anyway
        input("\n{}\n\nroky: Terminating. Press enter . . .".format(e))
        return

    # Establish a connection to the parent console process socket
    try:
        sock.connect(('localhost', port))
    except Exception as e:
        sock.close()
        input("\n{}\n\nroky: Terminating. Press enter . . .".format(e))
        return

    print("roky: Connected\n\nRoku Debugger Helper. Type 'quit' to exit")

    # Process user input until a 'quit' command or fatal error occurs
    while True:
        userInput = ''

        # Read the next line of console input, passing it to the parent process
        try:
            # Read a line from the console. Note - this input will not have a line-ending character.
            userInput = input("> ")
        except (EOFError, KeyboardInterrupt):
            # An EOFError exception is generated when the user presses Ctrl/C.
            # Hopefully, the KeyboardInterrupt exception has been disabled by our custom SIGINT handler,
            # otherwise an exception will be thrown that we can't trap while handling the EOFError.
            print("roky: Break!")
            sock.send(b'break\r')
        except Exception as e:
            # Most likely the parent's console window was closed, killing off the parent
            input("\n{}\n\nroky: Terminating. Press enter . . .".format(e))
            break

        # When 'quit' is input, terminate this process and its parent
        try:
            if userInput.rstrip().lower() == 'quit':
                sock.send(b'quit\r')
                break

            # Send the user input to the parent process, adding a CR character since the console input did not contain one
            sock.send(userInput.encode() + b'\r')
        except Exception as e:
            input("\n{}\n\nroky: Terminating. Press enter . . .".format(e))
            break

    # Close the socket
    sock.shutdown(socket.SHUT_RDWR)
    sock.close()


if __name__ == '__main__':
    '''Dispatch to either parent or child process 'main' handler.'''

    # Child process will be spawned with args: roky.py --parent-port <port>
    # Otherwise, it's the parent process, and parentMain() will parse the args.
    if len(sys.argv) >= 3 and sys.argv[1] == '--parent-port':
        childMain(int(sys.argv[2]))
    else:
        parentMain()

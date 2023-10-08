uPyT (MicroPython Terminal/Tool)
================================

uPyT is a collection of basic command-line tools and a Python API for
programming and interacting with MicroPython devices.

**Disclaimer:** This tool is still of pre-release quality. Functionality is
intended to be correct and robust however user-interface niceties (e.g.
friendly error messages in the CLI tools) are often missing. In addition, some
features (e.g.  the serial terminal) are currently POSIX-only.


Why yet another MicroPython tool?
---------------------------------

This tool exists out of frustration with the ergonomics and lack of robustness
of existing MicroPython utilities (e.g.
[mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html) and
[rshell](https://github.com/dhylands/rshell)). Specifically, by contrast with
existing tools, uPyT:

* Correctly and efficiently handles transferring binary and text based data
  to/from the device.
* Implements propper flow-control and batching enabling large, or numerous
  files to be sent, receieved, deleted, enumerated etc. without running out of
  overflowing buffers or memory.
* Implements an efficient file synchronisation mechanism which only transmits
  changes to the device.
* (Optionally) integrates file synchronisation with a serial terminal to avoid
  the need to constantly stop and start the terminal. Also ensures no terminal
  output is missed along the way.
* Automatically uses [paste
  mode](https://docs.micropython.org/en/latest/reference/repl.html#paste-mode)
  for multi-line pastes into the serial terminal.
* Is able to robustly kill running programs and reach the REPL (and raw-mode),
  robustly skipping past REPL-look-alike program output.
* Supports tab-completion of on-device filenames



Command line usage
------------------

uPyT's functionality is embodied as subcommands of the `upyt` command:

    $ upyt --device DEVICE-PATH SUBCOMMAND [ARGS]

NB: The `--device` argument comes *before* the subcommand.

A custom baudrate may be specified by suffixing the device path with, e.g.,
`:115200` where `115200` is your desired baudrate.

To avoid having to type `--device ...` before every command, you can set the
`UPYT_DEVICE` environment variable:

    $ export UPYT_DEVICE=/path/to/device

In the examples below we'll assume the `UPYT_DEVICE` environment variable is
set.


### Serial terminal

    $ upyt terminal

A simple serial terminal for monitoring device output or interacting with the
REPL. Use Ctrl+] to exit.

Notable features:

* If you paste multi-line text into the terminal, the terminal will
  automatically use MicroPython's [paste
  mode](https://docs.micropython.org/en/latest/reference/repl.html#paste-mode).
  This prevents the REPL's auto-indent features breaking the pasted code.

* The 'Ctrl+L' keyboard shortcut to clear the screen is emulated.

* Handles translation between the system's local text encoding and UTF-8 (as
  used by MicroPython).

See `--help` for further options.


### Rsync-style efficient file synchronisation

    $ upyt sync SOURCE-DIR [DEST-DIR]

Copy all of the files in a local source directory onto the device. If the
destination directory is omitted, the files are synchronised to the root
directory of the device. If given, device paths always begin with `:`, e.g.
`:/my/path`.

Add `--terminal` to enter the serial terminal immediately after the file sync
completes. Press Ctrl+R at any time to re-run synchronisation and then return
to the terminal. Press Ctrl+] to exit.

Only files which have changed are copied, and only a diff is transferred to the
device, dramatically speeding up subsequent sync operations. Extra files
present on the device but not the host are left in place: deletion is not
supported.

See [sync limitations](#sync-limitations) for limitations of the sync
mechanism.

Use `--exclude PATTERN` to exclude files or directories from sync. By default,
common 'junk' files (e.g. `.pyc` files) and version control litter (e.g.
`.git/`) are excluded.

See `--help` for further options.


### Basic filesystem operations

Familliar implementations of `ls`, `cat`, `mkdir`, `cp` and `rm` are provided.

For all of these commands, paths starting with `:` are interpreted as device
paths whilst those without are treated as host paths. Example usage:

    $ upyt cp /file/on/host.txt :/path/on/device  # Copy to device
    $ upyt cp :/file/on/device.txt /path/on/host  # Copy from device


### Device control utilities

A handful of utilities for controlling a MicroPython device are also provided:

    $ upyt interrupt

Robustly kill a running program, returning to the REPL.

    $ upyt reset

Soft reset MicroPython. Add the `--repl` option to skip execution of `main.py`
and reset straight into the REPL.


### Tab completion

Source [`upyt_complete.sh`](upyt_complete.sh) to enable commandline tab
completion in BASH-compatible shells.

*Warning:* completing on-device filenames requires uPyT to interrupt any
running program to perform directory listings. To disable this feature of the
tab completer, set `UPYT_NO_COMPLETE_PATHS` to any non-empty value.


Limitations
-----------

uPyT has a number of limitations. Some of these are simply down to immaturity
but others are a limitation of MicroPython.


### Sync limitations

The sync function works not by comparing the files on the MicroPython device
and the host, but by comparing the files on the host with a cache on the host.
(See the `.upyt_cache` directory.) As a result, if files are changed on the
device, sync may silently fail.

Whilst an rsync-like protocol -- which is based on comparing hashes and
timestamps -- would be ideal, it is problematic in practice. Firstly, most
MicroPython devices are quite slow and computing hashes takes appreciable time.
Secondly, many devices lack a real-time clock and so file timestamps are
unreliable. Finally, uploading (and compiling) an rsync implementation prior to
each sync operation would also introduce a non-trivial delay.

In practice, it is hoped that the main application of the sync function will be
in uploading code and static data to a device. As such, the files being synced
shouldn't change under your feet.

The sync command also takes some steps to safeguard against obvious mistakes.
Synced directories on the device include a `.upyt_id.txt` file which includes a
unique identifier for the device and a change number. This allows the sync
command to correctly handle syncing files to multiple devices and detect
situations where two computers have synced to the same device.


### Command line tool friendly errors

The commandline tools do not currently produce friendly errors when misused.
Instead expect a bare exception: often an opaque "IOError".  There is no
particular reason for this -- I'm just lazy.


### Windows support

The serial terminal feature (included the `--terminal` mode of the sync
command) is not supported on Windows. Other commands should work -- though this
is entirely untested.

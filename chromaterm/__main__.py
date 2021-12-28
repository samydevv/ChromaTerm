'''The command line utility for ChromaTerm'''
import argparse
import atexit
import io
import os
import re
import select
import signal
import sys

import yaml

from chromaterm import Color, Config, Palette, Rule

# Some sections are rarely used, so avoid unnecessary imports to speed up launch
# pylint: disable=import-outside-toplevel

# Possible locations of the config file, without the extension. Most-specific
# locations lead in the list.
CONFIG_LOCATIONS = [
    '~/.chromaterm',
    os.getenv('XDG_CONFIG_HOME', '~/.config') + '/chromaterm/chromaterm',
    '/etc/chromaterm/chromaterm',
]

# The frequency to check the child process' `cwd` and update our own
CWD_UPDATE_INTERVAL = 1 / 4

# Maximum chuck size per read
READ_SIZE = 4096  # 4 KiB

# Sequences upon which ct will split during processing. This includes new lines,
# vertical spaces, form feeds, C1 set (ECMA-048), SCS (G0 through G3 sets),
# CSI (excluding SGR), and OSC.
SPLIT_RE = re.compile(br'(\r\n?|\n|\v|\f|\x1b[\x40-\x5a\x5c\x5e\x5f]|'
                      br'\x1b[\x28-\x2b\x2d-\x2f][\x20-\x7e]|'
                      br'\x1b\x5b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x6c\x6e-\x7e]|'
                      br'\x1b\x5d[^\x07\x1b]*(?:\x07|\x1b\x5c)?)')


def args_init(args=None):
    '''Returns the parsed arguments (an instance of argparse.Namespace).

    Args:
        args (list): A list of program arguments, Defaults to sys.argv.
    '''
    parser = argparse.ArgumentParser()
    parser.epilog = 'For more info, go to https://github.com/hSaria/ChromaTerm.'

    parser.add_argument('program',
                        metavar='program ...',
                        nargs='?',
                        help='run a program with anything after it used as '
                        'arguments')

    parser.add_argument('arguments',
                        nargs=argparse.REMAINDER,
                        help=argparse.SUPPRESS)

    parser.add_argument('--benchmark',
                        action='store_true',
                        help='at exit, print rule usage statistics')

    parser.add_argument('--config',
                        metavar='FILE',
                        help='override config file location')

    parser.add_argument('--reload',
                        action='store_true',
                        help='reload the config of all CT instances')

    parser.add_argument('--rgb',
                        action='store_true',
                        help='use RGB colors (default: detect support, '
                        'fallback to xterm-256)')

    return parser.parse_args(args=args)


def eprint(*args, **kwargs):
    '''Prints a message to stderr.'''
    # Use \r\n to move to beginning of the new line in raw mode
    print('ct:', *args, end='\r\n', file=sys.stderr, **kwargs)


def get_default_config_location():
    '''Returns the first location in `CONFIG_LOCATIONS` that points to a file,
    defaulting to the first item in the list.
    '''
    resolve = lambda x: os.path.expanduser(os.path.expandvars(x))

    for location in CONFIG_LOCATIONS:
        for extension in ['.yml', '.yaml']:
            path = resolve(location + extension)

            if os.path.isfile(path):
                return path

    # No file found; default to the most-specific location
    return resolve(CONFIG_LOCATIONS[0] + '.yml')


def get_wait_duration(buffer, min_wait=1 / 256, max_wait=1 / 8):
    '''Returns the duration (float) to wait for more data before the last chunk
    of the received data can be processed independently.

    ChromaTerm cannot determine if it's processing data faster than input rate or
    if the input has finished. Therefore, ChromaTerm waits before processing the
    last chunk in the buffer. The waiting is cancelled if data becomes available.
    1/256 is smaller (shorter) than the fastest key repeat (1/255 second). 1/8th
    of a second is generally enough to account for the latency of command
    processing or a remote connection.

    Args:
        buffer (bytes): The incoming data that was processed.
        min_wait (float): Lower-bound on the returned duration.
        max_wait (float): Upper-bound on the returned duration.
    '''
    # New lines indicate long output; relax the wait duration
    if b'\n' in buffer:
        return max_wait

    return min_wait


def load_config(config, data, rgb=False):
    '''Reads configuration from a YAML-based string, formatted like so:
    palette:
      red: "#ff0000"
      blue: "#0000ff"

    rules:
    - description: My first rule
        regex: Hello
        color: b.red
    - description: My second rule is specfic to groups
        regex: W(or)ld
        color:
        0: f#321321
        1: bold

    Any errors are printed to stderr.

    Args:
        config (chromaterm.Config): The instance to which data is loaded.
        data (str): A string containg YAML data.
        rgb (bool): Whether the terminal is RGB-capable or not.
    '''
    try:
        data = yaml.safe_load(data) or {}
    except yaml.YAMLError as exception:
        eprint('Parse error:', exception)
        return

    palette = data.get('palette') if isinstance(data, dict) else None
    rules = data.get('rules') if isinstance(data, dict) else None

    if palette is not None:
        if not isinstance(palette, dict):
            eprint('Parse error: "palette" is not a dictionary')
            return

        palette = parse_palette(palette)

        if not isinstance(palette, Palette):
            eprint(palette)
            return

    if rules is None:
        eprint('Parse error: "rules" list not found in configuration')
        return

    if not isinstance(rules, list):
        eprint('Parse error: "rules" is not a list')
        return

    config.rules.clear()

    for rule in rules:
        rule = parse_rule(rule, palette=palette, rgb=rgb)

        if isinstance(rule, Rule):
            config.rules.append(rule)
        else:
            eprint(rule)

    # Put the non-overlapping (exclusive=True) rules at the top
    config.rules = sorted(config.rules, key=lambda x: not x.exclusive)


def parse_palette(data):
    '''Returns an instance of chromaterm.Palette if parsed correctly. Otherwise,
    a string with the error message is returned.

    Args:
        data (dict): A dictionary representing the color palette, formatted
            according to `chromaterm.__main__.load_config`.
    '''
    palette = Palette()

    try:
        for name, value in data.items():
            palette.add_color(name, value)
    except (TypeError, ValueError) as exception:
        return f'Error on palette color {repr(name)}: {repr(value)}: {exception}'

    return palette


def parse_rule(data, palette=None, rgb=False):
    '''Returns an instance of chromaterm.Rule if parsed correctly. Otherwise, a
    string with the error message is returned.

    Args:
        data (dict): A dictionary representing the rule, formatted according to
            `chromaterm.__main__.load_config`.
        palette (chromaterm.Palette): A palette to resolve colors.
        rgb (bool): Whether the terminal is RGB-capable or not.
    '''
    if not isinstance(data, dict):
        return f'Rule {repr(data)} not a dictionary'

    description = data.get('description')
    exclusive = data.get('exclusive', False)
    regex = data.get('regex')
    color = data.get('color')
    rule_repr = f'Rule(regex={repr(str(regex)[:30])})'

    if description:
        rule_repr = rule_repr[:-1] + f', description={repr(description)})'

    if not isinstance(color, dict):
        color = {0: color}

    try:
        for group, value in color.items():
            color[group] = Color(value, palette=palette, rgb=rgb)

        return Rule(regex, color, description, exclusive)
    except (TypeError, ValueError) as exception:
        return f'Error on {rule_repr}: {exception}'
    except re.error as exception:
        return f'Error on {rule_repr}: re.error: {exception}'


def process_input(config, data_fd, forward_fd=None, max_wait=None):
    '''Processes input by reading from data_fd, highlighting it using config,
    then printing it to sys.stdout. If forward_fd is not None, any data it has
    will be written (forwarded) into data_fd.

    Args:
        config (chromaterm.Config): Used for highlighting the data.
        data_fd (int): File descriptor to be read and highlighted.
        forward_fd (int): File descriptor to forwarded into data_fd. This is used
            in conjunction with run_program. None indicates forwarding is not
            required.
        max_wait (float): The maximum time to wait with no data on either of the
            file descriptors. None will block until at least one ready to be read.
    '''
    # pylint: disable=too-many-branches
    # Avoid BlockingIOError when output to non-blocking stdout is too fast
    try:
        os.set_blocking(sys.stdout.fileno(), True)
    except io.UnsupportedOperation:
        # In case stdout is replaced with StringIO
        pass

    fds = [data_fd] if forward_fd is None else [data_fd, forward_fd]
    buffer = b''

    ready_fds = read_ready(*fds, timeout=max_wait)

    while ready_fds:
        # There's some data to forward to the spawned program
        if forward_fd in ready_fds:
            try:
                os.write(data_fd, os.read(forward_fd, READ_SIZE))
            except OSError:
                # Spawned program or stdin closed; don't forward anymore
                fds.remove(forward_fd)

        # Data to be highlighted was received
        if data_fd in ready_fds:
            try:
                data_read = os.read(data_fd, READ_SIZE)
            except OSError:
                data_read = b''

            buffer += data_read

            # Buffer was processed empty and data fd hit EOF
            if not buffer:
                break

            chunks = split_buffer(buffer)

            # Process chunks except for the last one as it might've been cut off
            for data, separator in chunks[:-1]:
                sys.stdout.buffer.write(config.highlight(data) + separator)

            # Flush as `read_ready` might delay the flush at the bottom
            sys.stdout.flush()

            # Wait is cancelled when the user is typing; relax the wait duration
            if forward_fd is not None:
                wait_duration = get_wait_duration(buffer, min_wait=1 / 64)
            else:
                wait_duration = get_wait_duration(buffer)

            data, separator = chunks[-1]

            # Separator is an incomplete OSC; wait for a bit
            if data_read and separator.startswith(b'\x1b\x5d'):
                buffer = data + separator
            # Zero or one characters indicates keyboard typing; don't highlight
            # Account for the backspaces added by some shells, like zsh
            elif len(data) < 2 + data.count(b'\b') * 2:
                sys.stdout.buffer.write(data + separator)
                buffer = b''
            # There's more data; fetch it before highlighting
            elif data_read and read_ready(*fds, timeout=wait_duration):
                buffer = data + separator
            else:
                sys.stdout.buffer.write(config.highlight(data) + separator)
                buffer = b''

            # Flush as the last chunk might not end with a new line
            sys.stdout.flush()

        ready_fds = read_ready(*fds, timeout=max_wait)


def read_file(location):
    '''Returns the contents of a file or `None` on error. The error is printed
    to stderr.

    Args:
        location (str): The location of the file to be read.
    '''
    if not os.access(location, os.F_OK):
        eprint(f'Configuration file {repr(location)} not found')
        return None

    try:
        with open(location, 'r', encoding='utf-8') as file:
            return file.read()
    except PermissionError:
        eprint(f'Cannot read configuration file {repr(location)} (permission)')
        return None


def read_ready(*fds, timeout=None):
    '''Returns a list of file descriptors that are ready to be read.

    Args:
        *fds (int): Integers that refer to the file descriptors.
        timeout (float): Passed to `select.select`.
    '''
    return [] if not fds else select.select(fds, [], [], timeout)[0]


def reload_chromaterm_instances():
    '''Reloads other ChromaTerm CLI instances by sending them `signal.SIGUSR1`.

    Returns:
        The number of processes reloaded.
    '''
    import psutil

    count = 0
    current_process = psutil.Process()

    for process in psutil.process_iter():
        # Don't reload the current process
        if process.pid == current_process.pid:
            continue

        try:
            # Only compare the first two arguments (Python and script paths)
            if process.cmdline()[:2] == current_process.cmdline()[:2]:
                os.kill(process.pid, signal.SIGUSR1)
                count += 1
        # As per the docs, expect those errors when accessing the process methods
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    return count


def run_program(program_args):
    '''Spawns a program in a pty fork to emulate a controlling terminal.

    Args:
        program_args (list): A list of program arguments. The first argument is
            the program name/location.

    Returns:
        A file descriptor (int) of the mater end of the pty fork.
    '''
    import fcntl
    import pty
    import termios
    import threading
    import time
    import tty

    try:
        # Set to raw as the pty will be handling any processing; restore at exit
        attributes = termios.tcgetattr(sys.stdin.fileno())
        atexit.register(termios.tcsetattr, sys.stdin.fileno(), termios.TCSANOW,
                        attributes)
        tty.setraw(sys.stdin.fileno())
    except termios.error:
        attributes = None

    # openpty, login_tty, then fork
    pid, master_fd = pty.fork()

    if pid == 0:
        # Update the slave's pty (now on standard fds) attributes
        if attributes:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)

        try:
            os.execvp(program_args[0], program_args)
        except FileNotFoundError:
            eprint(f'command not found: {program_args[0]}')

        # exec replaces the fork's process; only hit on exception
        sys.exit(1)
    else:
        # Update the slave's window size as the master is capturing the signals
        def window_resize_handler(*_):
            size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, '0000')
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)

        if sys.stdin.isatty():
            signal.signal(signal.SIGWINCH, window_resize_handler)
            window_resize_handler()

        # Some terminals update their titles based on `cwd` (see #94)
        def update_cwd():  # pragma: no cover
            # Covered by `test_main_cwd_tracking` but not detected by coverage
            import psutil

            try:
                child_process = psutil.Process(pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return

            while True:
                try:
                    time.sleep(CWD_UPDATE_INTERVAL)
                    os.chdir(child_process.cwd())
                except (OSError, psutil.AccessDenied, psutil.NoSuchProcess):
                    pass

        # Daemonized to exit immediately with ChromaTerm
        threading.Thread(target=update_cwd, daemon=True).start()

        return master_fd


def split_buffer(buffer):
    '''Returns a tuple of tuples in the format of (data, separator). data should
    be highlighted while separator should be printed, unchanged, after data.

    Args:
        buffer (bytes): Data to split using `SPLIT_RE`.
    '''
    chunks = SPLIT_RE.split(buffer)

    # Append an empty separator when chunks end with data
    if chunks[-1]:
        chunks.append(b'')

    # Group all chunks into format of (data, separator)
    return tuple(zip(chunks[0::2], chunks[1::2]))


def main(args=None, max_wait=None, write_default=True):
    '''Command line utility entry point.

    Args:
        args (list): A list of program arguments. Defaults to sys.argv.
        max_wait (float): The maximum time to wait with no data. None will block
            until data is ready to be read.
        write_default (bool): Whether to write the default configuration or not.
            Only written if it doesn't exist already.

    Returns:
        A string indicating status/error. Otherwise, returns None.
    '''
    args = args_init(args)

    if args.reload:
        return f'Processes reloaded: {reload_chromaterm_instances()}'

    # Config file wasn't overridden; use default file
    if not args.config:
        args.config = get_default_config_location()

        # Write default config if not there
        if write_default and not os.access(args.config, os.F_OK):
            from chromaterm.default_config import write_default_config
            write_default_config(args.config)

    config = Config(benchmark=args.benchmark)

    # Print benchmark after cleanup in `run_program`
    if args.benchmark:
        atexit.register(config.print_benchmark_results)

    # Create the signal handler to trigger reloading the config
    def reload_config_handler(*_):
        config_data = read_file(args.config)

        if config_data:
            load_config(config, config_data, rgb=args.rgb or None)

    # Trigger the initial loading
    reload_config_handler()

    if args.program:
        # ChromaTerm is spawning the program in a controlling terminal; stdin is
        # being forwarded to the program
        data_fd = run_program([args.program] + args.arguments)
        forward_fd = sys.stdin.fileno()
    else:
        # Data is being piped into ChromaTerm's stdin; no forwarding needed
        data_fd = sys.stdin.fileno()
        forward_fd = None

    # Ignore SIGINT (CTRL+C) and attach reload handler
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGUSR1, reload_config_handler)

    try:
        # Begin processing the data (blocking operation)
        process_input(config, data_fd, forward_fd, max_wait)
    except BrokenPipeError:
        # Surpress the implicit flush that Python runs on exit
        sys.stdout.flush = lambda: None

    return None


if __name__ == '__main__':
    sys.exit(main())

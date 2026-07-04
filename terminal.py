# UNIX-Specific
import curses

import queue
import time
import audio

_FIXED_HEIGHT = 16
_FIXED_WIDTH = 64

_PAUSE_ICON = "\u23F8"
_PLAY_ICON = "\u23F5"
_NEXT_ICON = "\u23ED"

_VOLUME_MUTED_ICON = "\U0001F508"
_VOLUME_LOW_SOUND_ICON = "\U0001F509"
_VOLUME_HIGH_SOUND_ICON = "\U0001F50A"

_ACTIVE = 1
_MUTED = 2


def init_io(config: dict) -> None:
    """
    initialize the things necessary for io.
    Must call deinit_io to return the terminal to normal
    """
    config['stdscr'] = curses.initscr()
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(_ACTIVE, curses.COLOR_GREEN, -1)
    curses.init_pair(_MUTED, curses.COLOR_CYAN, -1)

    curses.noecho()
    curses.cbreak()
    curses.curs_set(0)
    config['stdscr'].keypad(True)
    config['stdscr'].timeout(100)
    config['win'] = _create_window(config['stdscr'])


def do_io(config: dict) -> None:
    """
    accepts user input and shows output;
    expects config to first be passed through init_io;
    """
    screen = {
        'title': "",
        'play': True,
        'next': None,
        'forward': None,
        'backward': None,
        'volume': audio.DEFAULT_VOLUME,
        'list_status': audio.ListStatus.NO_CHANGE,
        'help': [
            'q - Quit',
            'k - Pause/Play',
            'n - Next In Oueue',
            'r - Repeat One',
            's - Shuffle',
            'x - Sort',
            'l - Skip 5 Seconds',
            'j - Rewind 5 Seconds',
            '= - Volume Up',
            '- - Volume Down',
        ]
    }

    while not config['stop_event'].is_set():
        ch = config['stdscr'].getch()
        if ch == ord('q'):
            config['stop_event'].set()
        elif ch in (ord('k'), ord(' ')):
            config['command_queue'].put("pause/play")
        elif ch == ord('n'):
            config['command_queue'].put("next")
        elif ch == ord('l'):
            config['command_queue'].put("forward")
        elif ch == ord('j'):
            config['command_queue'].put("backward")
        elif ch == ord('='):
            config['command_queue'].put("volume_up")
        elif ch == ord('-'):
            config['command_queue'].put("volume_down")
        elif ch == ord('s'):
            config['command_queue'].put("shuffle")
        elif ch == ord('x'):
            config['command_queue'].put("sort")
        elif ch == ord('r'):
            config['command_queue'].put("repeat_one")
        if ch == curses.KEY_RESIZE:
            config['stdscr'].clear()
            del config['win']
            config['win'] = _create_window(config['stdscr'])
            _redraw(config, screen)

        try:
            output = config['io_queue'].get_nowait()
            if isinstance(output, dict):
                _update_screen(screen, output)
        except queue.Empty:
            # nothing to do
            pass

        _redraw(config, screen)


def deinit_io(config: dict) -> None:
    """
    returns the terminal to normal
    """
    if config['win'] is not None:
        del config['win']
    curses.nocbreak()
    config['stdscr'].keypad(False)
    curses.echo()
    curses.curs_set(1)
    curses.endwin()


def _update_screen(screen: dict, output: dict) -> None:
    if output['action'] == 'music':
        screen['title'] = output['title']
        return

    if output['action'] != 'command':
        raise UnknownActionError

    if output['to'] == 'play':
        screen['play'] = True
    elif output['to'] == 'pause':
        screen['play'] = False
    elif output['to'] == 'next':
        screen['next'] = time.time() + output.get('lifespan', 0.5)
    elif output['to'] == 'forward':
        screen['forward'] = time.time() + output.get('lifespan', 0.5)
    elif output['to'] == 'backward':
        screen['backward'] = time.time() + output.get('lifespan', 0.5)
    elif output['to'] == 'volume':
        screen['volume'] = output['volume']
    elif output['to'] == 'list_status':
        screen['list_status'] = output['list_status']


def _redraw(config: dict, screen: dict) -> None:
    if config['win'] is None:
        config['stdscr'].move(1, 0)
        config['stdscr'].clrtoeol()
        config['stdscr'].addstr(1, 0, _get_display_name(screen['title']))
        return

    # Pause / Play Icon
    config['win'].addch(3, 2, _PAUSE_ICON if screen['play'] else _PLAY_ICON)

    # Next Icon
    if screen['next'] is not None and screen['next'] <= time.time():
        # screen time has already passed
        screen['next'] = None
    if screen['next'] is not None:
        config['win'].addch(3, 8, _NEXT_ICON, curses.color_pair(_ACTIVE))
    else:
        config['win'].addch(3, 8, _NEXT_ICON)

    # Title
    config['win'].addstr(3, 16, _get_display_name(screen['title']))

    # Volume
    if _approx_equals(screen['volume'], 0.0):
        volume_icon = _VOLUME_MUTED_ICON
    elif screen['volume'] < 1.1:
        volume_icon = _VOLUME_LOW_SOUND_ICON
    else:
        volume_icon = _VOLUME_HIGH_SOUND_ICON
    config['win'].addstr(3, 55, f"{volume_icon} {screen['volume']:0.2f}")

    # Rewind
    if screen['backward'] is not None and screen['backward'] <= time.time():
        # screen time has already passed
        screen['backward'] = None
    if screen['backward'] is not None:
        config['win'].addstr(5, 1, "[-5]", curses.color_pair(_ACTIVE))
    else:
        config['win'].addstr(5, 1, "[-5]", curses.color_pair(_MUTED))

    # Skip
    if screen['forward'] is not None and screen['forward'] <= time.time():
        # screen time has already passed
        screen['forward'] = None
    if screen['forward'] is not None:
        config['win'].addstr(5, 7, "[+5]", curses.color_pair(_ACTIVE))
    else:
        config['win'].addstr(5, 7, "[+5]", curses.color_pair(_MUTED))

    # List Status
    if screen['list_status'] == audio.ListStatus.SORT:
        config['win'].addstr(5, 54, "[sort   ]", curses.color_pair(_ACTIVE))
    elif screen['list_status'] == audio.ListStatus.SHUFFLE:
        config['win'].addstr(5, 54, "[shuffle]", curses.color_pair(_ACTIVE))
    elif screen['list_status'] == audio.ListStatus.REPEAT_ONE:
        config['win'].addstr(5, 54, "[repeat ]", curses.color_pair(_ACTIVE))
    else:
        config['win'].addstr(5, 54, "[normal ]", curses.color_pair(_MUTED))

    for i in range(len(screen['help']) // 2):
        config['win'].addstr(
            8 + i,
            2,
            screen['help'][2*i],
            curses.color_pair(_MUTED),
        )

        config['win'].addstr(
            8 + i,
            38,
            screen['help'][2*i + 1],
            curses.color_pair(_MUTED),
        )
    if len(screen['help']) % 2 == 1:
        config['win'].addstr(
            8 + len(screen['help'])//2 + 1,
            2,
            screen['help'][-1],
            curses.color_pair(_MUTED),
        )

    config['win'].refresh()


def _create_window(stdscr: curses.window) -> curses.window | None:
    screen_height, screen_width = stdscr.getmaxyx()
    if screen_height < _FIXED_HEIGHT or screen_width < _FIXED_WIDTH:
        stdscr.addstr(
            0,
            0,
            f"For a proper view, please use a terminal of size {_FIXED_WIDTH}x{_FIXED_HEIGHT} at least",
        )
        return None

    start_y = (screen_height - _FIXED_HEIGHT) // 2
    start_x = (screen_width - _FIXED_WIDTH) // 2
    win = curses.newwin(_FIXED_HEIGHT, _FIXED_WIDTH, start_y, start_x)
    win.keypad(True)
    win.timeout(100)
    return win


def _get_display_name(name: str) -> str:
    if len(name) <= 32:
        return name + ' ' * (32 - len(name))
    return name[:29] + "..."


def _approx_equals(a: float, b: float, minimum_difference: float = 0.025) -> bool:
    return abs(a - b) < minimum_difference


class UnknownActionError(BaseException):
    pass

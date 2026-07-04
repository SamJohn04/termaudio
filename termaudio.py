"""
Play audio offline

only works on linux right now
"""

import sys
import threading
import queue

import audio
import terminal


def init(paths: list[str]) -> dict:
    """
    initialize the data necessary
    """
    config = {}

    # init audio data
    audio.init_audio(config, paths)

    # init screen
    terminal.init_io(config)

    # init threading
    config['stop_event'] = threading.Event() # tells everyone to stop
    config['command_queue'] = queue.Queue() # io > audio
    config['io_queue'] = queue.Queue() # audio > io

    return config


def deinit(config: dict) -> None:
    """
    deinitialize as necessary
    """
    # deinit threading
    config['stop_event'].set()

    # deinit audio
    audio.deinit_audio(config)

    # deinit screen
    terminal.deinit_io(config)
    

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("expected: termaudio PATH ...")
        sys.exit(1)

    config = init(sys.argv[1:])
    audio_thread = None
    io_thread = None
    try:
        audio_thread = threading.Thread(target=audio.play, args=(config, ))
        io_thread = threading.Thread(target=terminal.do_io, args=(config, ))

        audio_thread.start()
        io_thread.start()

        io_thread.join()
        audio_thread.join()
    except KeyboardInterrupt:
        config['stop_event'].set()
        if io_thread is not None:
            io_thread.join()
        if audio_thread is not None:
            audio_thread.join()
    finally:
        deinit(config)

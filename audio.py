import enum
from pathlib import Path
import random
import sys
import time

import queue
import threading

import audioop
import pyaudio
from pydub import AudioSegment

DEFAULT_VOLUME = 1.0
CHUNK_SIZE = 4096


def init_audio(config: dict, paths: list[str]) -> None:
    """
    initialize the things necessary for audio;
    must call deinit_audio to stop pyaudio
    """
    print("--- initializing pyaudio ---")
    config['pyaudio'] = pyaudio.PyAudio()
    print("--- pyaudio initialization complete ---\n")

    config['sources'] = []
    for path in paths:
        try:
            config['sources'].extend(_find_music_files(path))
        except PathNotExistsException:
            print(f"[ERROR] {path} does not exist", file=sys.stderr)
        except PathNotRecognizedException:
            print(f"[ERROR] {path} is not a file or a directory", file=sys.stderr)


def play(config: dict) -> None:
    """
    plays audio;
    expects config dict to first be passed through init_audio;
    """
    options = {
        'volume': DEFAULT_VOLUME,
        'list_status': ListStatus.NO_CHANGE,
        'next': False,
    }
    to_delete = []

    while not config['stop_event'].is_set():
        if not config['sources']:
            config['stop_event'].set()
            return

        for source in config['sources']:
            while True:
                try:
                    config['io_queue'].put({"action": "music", "title": source.name})
                    audio = AudioSegment.from_file(source)
                    options = _play_song(
                        config['pyaudio'],
                        audio,
                        options,
                        config['stop_event'],
                        config['command_queue'],
                        config['io_queue'],
                    )
                except UnsupportedRawDataOnAudioSegmentException:
                    config['io_queue'].put(f"{source.name} has unsupported raw data")
                    to_delete.append(source)
                    break
                except:
                    config['io_queue'].put(f"{source} is not a music file")
                    to_delete.append(source)
                    break

                if config['stop_event'].is_set():
                    return
                if options['list_status'] != ListStatus.REPEAT_ONE or options['next']:
                    options['next'] = False # reset for the next song
                    break

            if options['list_status'] not in (ListStatus.NO_CHANGE, ListStatus.REPEAT_ONE):
                break

        if options['list_status'] == ListStatus.SHUFFLE:
            random.shuffle(config['sources'])
        elif options['list_status'] == ListStatus.SORT:
            config['sources'].sort()
        options['list_status'] = ListStatus.NO_CHANGE
        config['io_queue'].put({"action": "command", "to": "list_status", "list_status": options['list_status']})

        for item in to_delete:
            config['sources'].remove(item)
        to_delete.clear()


def deinit_audio(config: dict) -> None:
    """
    deinitiallize audio
    """
    config['pyaudio'].terminate()


class ListStatus(enum.Enum):
    """
    ListStatus enum to say whether the list is shuffled, sorted, or neither
    used as list_status field in io_queue
    """
    NO_CHANGE = 0
    SHUFFLE = 1
    SORT = 2
    REPEAT_ONE = 4


def _find_music_files(path_str: str) -> list[Path]:
    """
    if path exists and is a file, returns the list containing the Path of the file;
    if path exists and is a directory, return all the children that have an mp3 or wav extension;
    throws an error otherwise:
        PathNotExistsException if the path does not exist
        PathNotRecognizedException if the path is not a file or a directory
    """
    path = Path(path_str)
    sources_in_path = []

    # check if path exists
    if not path.exists():
        raise PathNotExistsException

    if path.is_file():
        sources_in_path.append(path)
        return sources_in_path

    if not path.is_dir():
        # path is something like fifo, and we dont deal with that
        raise PathNotRecognizedException

    for child_path in path.iterdir():
        if not child_path.is_file():
            continue
        child_path_str = str(child_path)
        # if child path is .wav or .mp3
        if child_path_str.endswith(".wav") or child_path_str.endswith(".mp3"):
            sources_in_path.append(child_path)

    return sources_in_path


def _play_song(
    p: pyaudio.PyAudio,
    seg: AudioSegment,
    options: dict,
    stop_event: threading.Event,
    command_queue: queue.Queue,
    io_queue: queue.Queue,
) -> dict:
    """
    plays the song that is passed in;
    if the raw_data of AudioSegment is not of type bytes, throws an error;
    expects options['volume'] to be a float from 0.0 to 2.0; value gets clamped if user requests a volume change;
    error:
        UnsupportedRawDataOnAudioSegmentException if raw_data is not of type bytes
    """
    rate = seg.frame_rate
    channels = seg.channels
    sample_width = seg.sample_width
    bytes_per_frame = channels * sample_width
    bytes_per_chunk = bytes_per_frame * CHUNK_SIZE
    one_second_in_bytes = rate * bytes_per_frame

    raw_data = seg.raw_data
    if not isinstance(raw_data, bytes):
        raise UnsupportedRawDataOnAudioSegmentException

    pause_event = threading.Event() # for set/reset here and reading in callback

    volume = options['volume']
    volume_lock = threading.Lock() # needed in callback as well as to increase/decrease volume

    i = 0
    i_lock = threading.Lock() # needed in callback as well as to skip

    def callback(*_):
        nonlocal i
        with i_lock:
            if pause_event.is_set():
                return b'\x00' * bytes_per_chunk, pyaudio.paContinue
            data = raw_data[i:i+bytes_per_chunk]
            i += bytes_per_chunk

        if data:
            data = audioop.mul(data, sample_width, volume)

        if len(data) < bytes_per_chunk:
            return (data, pyaudio.paComplete)
        return (data, pyaudio.paContinue)

    stream = p.open(
        format=p.get_format_from_width(sample_width),
        channels=channels,
        rate=rate,
        frames_per_buffer=CHUNK_SIZE,
        output=True,
        stream_callback=callback,
    )

    while not stop_event.is_set() and stream.is_active():
        try:
            cmd = command_queue.get_nowait()
            if cmd == "pause/play":
                if pause_event.is_set():
                    pause_event.clear()
                    io_queue.put({"action": "command", "to": "play"})
                else:
                    pause_event.set()
                    io_queue.put({"action": "command", "to": "pause"})

            elif cmd == "next":
                # we are closing this stream under the assumption that
                # the next song will be played automatically
                _safe_close_stream(stream)
                options['next'] = True
                io_queue.put({"action": "command", "to": "next", "lifespan": 1})
                return options

            elif cmd == "forward":
                # go 5s forward
                with i_lock:
                    i = min(i + 5 * one_second_in_bytes, len(raw_data))
                io_queue.put({"action": "command", "to": "forward", "lifespan": 1})

            elif cmd == "backward":
                # go 5s back
                with i_lock:
                    i = max(i - 5 * one_second_in_bytes, 0)
                io_queue.put({"action": "command", "to": "backward", "lifespan": 1})

            elif cmd == "volume_up":
                # increase volume by 0.1
                with volume_lock:
                    volume = min(volume + 0.1, 2.0)
                options['volume'] = volume
                io_queue.put({"action": "command", "to": "volume", "volume": volume})

            elif cmd == "volume_down":
                # decrease volume by 0.1
                with volume_lock:
                    volume = max(volume - 0.1, 0.0)
                options['volume'] = volume
                io_queue.put({"action": "command", "to": "volume", "volume": volume})

            elif cmd == "shuffle":
                # user clicking shuffle twice is the same as user not clicking it
                if options['list_status'] != ListStatus.SHUFFLE:
                    options['list_status'] = ListStatus.SHUFFLE
                else:
                    options['list_status'] = ListStatus.NO_CHANGE

                io_queue.put({"action": "command", "to": "list_status", "list_status": options['list_status']})

            elif cmd == "sort":
                # user clicking sort twice is the same as user not clicking it
                if options['list_status'] != ListStatus.SORT:
                    options['list_status'] = ListStatus.SORT
                else:
                    options['list_status'] = ListStatus.NO_CHANGE

                io_queue.put({"action": "command", "to": "list_status", "list_status": options['list_status']})

            elif cmd == "repeat_one":
                # user clicking repeat_one twice is the same as user not clicking it
                if options['list_status'] != ListStatus.REPEAT_ONE:
                    options['list_status'] = ListStatus.REPEAT_ONE
                else:
                    options['list_status'] = ListStatus.NO_CHANGE

                io_queue.put({"action": "command", "to": "list_status", "list_status": options['list_status']})

        except queue.Empty:
            pass # nothing to do

        time.sleep(0.1)

    _safe_close_stream(stream)
    return options


def _safe_close_stream(stream: pyaudio.Stream):
    try:
        if stream.is_active():
            stream.stop_stream()
        stream.close()
    except OSError:
        # already closed; do nothing
        pass


class PathNotExistsException(BaseException):
    pass


class PathNotRecognizedException(BaseException):
    pass


class UnsupportedRawDataOnAudioSegmentException(BaseException):
    pass

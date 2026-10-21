import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import reelagent.render.composer as c
print("import ok:", c.__file__)
import inspect
src = inspect.getsource(c.compose_mp4)
print("compose_mp4 含 encoding:", 'encoding="utf-8"' in src)
print("has_audio_stream 含 encoding:", 'encoding="utf-8"' in inspect.getsource(c.has_audio_stream))
print("compose 降级已包 try:", "except RenderError as exc" in inspect.getsource(c.compose))

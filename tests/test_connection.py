import pytest

import time

from upyt.connection import SubprocessConnection


class TestSubprocessConnection:
    
    def test_closed(self) -> None:
        c = SubprocessConnection(["true"])
        c._process.wait()
        
        assert c.read(10) == b""
        assert c.read_until(b"foo") == b""
        assert c.read_buffered() == b""
        
        assert c.write(b"foo") == 0
    
    def test_read(self) -> None:
        c = SubprocessConnection(["echo", "hello"])
        
        assert c.read(3) == b"hel"
        assert c.read(1) == b"l"
        assert c.read(10) == b"o\n"
    
    def test_read_timeout(self) -> None:
        c = SubprocessConnection(
            "echo hello; sleep 0.2; echo world; sleep 0.01; echo today",
            shell=True,
            bufsize=0,
            timeout=0.1,
        )
        
        before = time.time()
        assert c.read(10) == b"hello\n"
        after = time.time()
        assert after - before == pytest.approx(0.1, abs=0.05)
        
        time.sleep(0.1)
        
        assert c.read(20) == b"world\ntoday\n"
    
    def test_read_until(self) -> None:
        c = SubprocessConnection(["echo", "Hello! How are you?"])
        
        assert c.read_until(b"!") == b"Hello!"
        assert c.read_until(b" ") == b" "
        assert c.read_until(b"you?") == b"How are you?"
    
    def test_read_until_timeout(self) -> None:
        c = SubprocessConnection(["echo", "hello, world!"], timeout=0.1)
        
        before = time.time()
        assert c.read_until(b"?") == b"hello, world!\n"
        after = time.time()
        assert after - before < 0.1  # NB: Actually instant due to exit...
    
    def test_read_buffered(self) -> None:
        c = SubprocessConnection(["echo", "Hello, world!"])
        c._process.wait()
        
        assert c.read_buffered() == b"Hello, world!\n"
    
    def test_write(self) -> None:
        c = SubprocessConnection("read line; echo line=$line", shell=True)
        
        assert c.write(b"foo\n") == 4
        assert c.read(100) == b"line=foo\n"

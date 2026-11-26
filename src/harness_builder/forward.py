"""Inside a network-less sandbox: listen on 127.0.0.1:PORT and pass every connection
to the model exit's Unix socket. Standalone, standard library only.

usage: forward.py SOCKET_PATH PORT
"""
import socket
import socketserver
import sys
import threading


def pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            up.connect(self.server.target)
        except OSError:
            up.close()
            return
        t = threading.Thread(target=pump, args=(up, self.request), daemon=True)
        t.start()
        pump(self.request, up)
        t.join()
        up.close()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    server = Server(("127.0.0.1", int(sys.argv[2])), Handler)
    server.target = sys.argv[1]
    server.serve_forever()

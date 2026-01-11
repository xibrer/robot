"""
TCP视频流服务器
用于接收来自Android手机的视频流
"""
import socket
import struct
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage
import numpy as np
import cv2


class TcpCameraServer(QThread):
    """TCP摄像头服务器 - 接收来自Android的视频流"""
    frame_ready = pyqtSignal(int, QImage)  # 摄像头ID和帧数据
    
    def __init__(self, port: int = 8888):
        super().__init__()
        self.port = port
        self.running = False
        self.socket: socket.socket = None
        self.client_connections = {}  # camera_id -> (socket, thread)
        
    def run(self):
        """启动TCP服务器"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.listen(5)
            print(f"TCP视频流服务器启动，监听端口 {self.port}...")
            
            self.running = True
            
            while self.running:
                try:
                    client_socket, addr = self.socket.accept()
                    print(f"新客户端连接: {addr}")
                    client_socket.settimeout(5.0)
                    
                    # 接收摄像头ID（第一个字节）
                    camera_id_byte = client_socket.recv(1)
                    if not camera_id_byte:
                        client_socket.close()
                        continue
                        
                    camera_id = int.from_bytes(camera_id_byte, 'big')
                    camera_name = "CAM_HIGH" if camera_id == 0 else "CAM_LEFT_WRIST"
                    print(f"摄像头 {camera_name} (ID: {camera_id}) 已连接")
                    
                    # 为每个客户端创建接收线程
                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, camera_id, camera_name),
                        daemon=True
                    )
                    thread.start()
                    self.client_connections[camera_id] = (client_socket, thread)
                    
                except Exception as e:
                    if self.running:
                        print(f"接受连接错误: {e}")
                        
        except Exception as e:
            print(f"TCP服务器错误: {e}")
        finally:
            if self.socket:
                self.socket.close()
                
    def _handle_client(self, client_socket: socket.socket, camera_id: int, camera_name: str):
        """处理客户端连接"""
        try:
            while self.running:
                try:
                    # 接收帧大小（4字节，大端序）
                    size_data = self._recv_all(client_socket, 4)
                    if len(size_data) < 4:
                        break
                        
                    frame_size = struct.unpack('>I', size_data)[0]
                    
                    # 接收图像数据
                    image_data = self._recv_all(client_socket, frame_size)
                    if len(image_data) < frame_size:
                        print(f"{camera_name} 接收图像数据不完整: {len(image_data)}/{frame_size}")
                        break
                    
                    # 转换为QImage
                    image = QImage()
                    loaded = image.loadFromData(image_data)
                    
                    if loaded and not image.isNull():
                        self.frame_ready.emit(camera_id, image)
                        # print(f"DEBUG: {camera_name} 发送帧 {image.width()}x{image.height()}")
                    else:
                        print(f"DEBUG: {camera_name} 图片解码失败, 数据大小: {len(image_data)}")
                        
                except socket.timeout:
                    # print(f"DEBUG: {camera_name} 等待超时") 
                    continue
                except Exception as e:
                    print(f"{camera_name} 接收错误: {e}")
                    break
        finally:
            client_socket.close()
            if camera_id in self.client_connections:
                del self.client_connections[camera_id]
            print(f"{camera_name} 已断开连接")
                
    def _recv_all(self, sock: socket.socket, size: int) -> bytes:
        """接收指定大小的数据"""
        data = b''
        while len(data) < size and self.running:
            try:
                chunk = sock.recv(min(size - len(data), 4096))
                if not chunk:
                    return b''
                data += chunk
            except socket.timeout:
                continue
            except OSError:
                return b''
        return data
        
    def stop(self):
        """停止服务器"""
        self.running = False
        for camera_id, (sock, _) in list(self.client_connections.items()):
            try:
                sock.close()
            except:
                pass
        self.client_connections.clear()
        if self.socket:
            try:
                self.socket.close()
            except:
                pass

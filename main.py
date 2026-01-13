import sys
import cv2
import time
import numpy as np
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFrame, QDesktopWidget, QSizePolicy,
    QGridLayout
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QThread
from PyQt5.QtGui import QImage, QPixmap, QFont, QPainter

# 确保 tcp_camera_server.py 在同一目录下
try:
    from tcp_camera_server import TcpCameraServer
except ImportError:
    print("Warning: tcp_camera_server module not found. TCP features will be disabled.")
    TcpCameraServer = None


class VideoLabel(QLabel):
    """ 自定义视频显示控件 """
    def __init__(self, text=""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setStyleSheet("background-color: black; border: 2px solid #444;")
        self.setAlignment(Qt.AlignCenter)
        self._pixmap = None

    def set_image(self, q_image):
        self._pixmap = QPixmap.fromImage(q_image)
        self.update()

    def paintEvent(self, event):
        if not self._pixmap:
            super().paintEvent(event)
            return
        rect = self.rect()
        scaled_pixmap = self._pixmap.scaled(
            rect.size(), 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        x = (rect.width() - scaled_pixmap.width()) // 2
        y = (rect.height() - scaled_pixmap.height()) // 2
        painter = QPainter(self)
        painter.drawPixmap(x, y, scaled_pixmap)


class CameraThread(QThread):
    """ 本地摄像头采集线程 """
    frame_ready = pyqtSignal(int, QImage)
    
    def __init__(self, camera_id: int, camera_name: str):
        super().__init__()
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.running = False
        self.cap: Optional[cv2.VideoCapture] = None
        
    def run(self):
        self.cap = cv2.VideoCapture(self.camera_id)
        if not self.cap.isOpened():
            return
        self.running = True
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                bytes_per_line = ch * w
                qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.frame_ready.emit(self.camera_id, qt_image)
            time.sleep(0.033)
            
    def stop(self):
        self.running = False
        self.wait()
        if self.cap:
            self.cap.release()


class MockRobotThread(QThread):
    """
    模拟机械臂数据线程 (7维)
    """
    robot_data_signal = pyqtSignal(float, np.ndarray)

    def __init__(self):
        super().__init__()
        self.running = False
        self.start_time = 0.0
        self.phases = np.random.rand(7) * 2 * np.pi
        self.freqs = np.random.rand(7) * 0.5 + 0.1

    def run(self):
        self.running = True
        self.start_time = time.time()
        print("Mock Robot Connected.")
        
        while self.running:
            current_time = time.time()
            elapsed = current_time - self.start_time
            qpos = np.sin(elapsed * self.freqs + self.phases)
            self.robot_data_signal.emit(current_time, qpos)
            time.sleep(0.02) # 50Hz

    def stop(self):
        self.running = False
        self.wait()


class DataCollectionApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dataset_name = "my_dataset_v1"
        self.is_recording = False
        self.record_start_time = None
        
        # --- 任务信息 ---
        self.task_instruction = "" 
        self.task_type = ""        
        self.instruction_locked = False
        
        # --- 数据缓冲区 ---
        self.output_dir = Path("datasets") / self.dataset_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frame_count = 0
        self.video_writers = {}
        self.robot_data_buffer: List[tuple] = []
        
        # --- 线程管理 ---
        self.camera_threads = {}
        self.tcp_server = None
        self.robot_thread = None
        
        self.init_ui()
        self.init_timers()
        self.start_threads()
        
    def init_ui(self):
        self.setWindowTitle("机器人数据采集系统 (7-DOF Mock)")
        window_width = 1600
        window_height = 1000
        screen = QDesktopWidget().screenGeometry()
        x = (screen.width() - window_width) // 2
        y = (screen.height() - window_height) // 2
        self.setGeometry(x, y, window_width, window_height)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 1. 摄像头区域
        camera_area = self.create_camera_area()
        main_layout.addWidget(camera_area, stretch=1) 
        
        # 2. 控制台区域
        console_area = self.create_console_area()
        main_layout.addWidget(console_area, stretch=0)
        
    def create_camera_area(self):
        camera_frame = QFrame()
        camera_frame.setFrameShape(QFrame.Box)
        camera_frame.setStyleSheet("background-color: #333;")
        camera_layout = QHBoxLayout(camera_frame)
        camera_layout.setSpacing(10)
        camera_layout.setContentsMargins(10, 10, 10, 10)
        
        left_camera = self.create_camera_view("主视图")
        camera_layout.addWidget(left_camera, 1)
        
        right_camera = self.create_camera_view("左视图")
        camera_layout.addWidget(right_camera, 1)
        
        return camera_frame
        
    def create_camera_view(self, description: str):
        camera_widget = QWidget()
        camera_widget.setStyleSheet("background-color: #222; border-radius: 4px;")
        camera_layout = QVBoxLayout(camera_widget)
        camera_layout.setContentsMargins(5, 5, 5, 5)
        
        title_label = QLabel(f"{description}")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFixedHeight(50)
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setStyleSheet("color: #ddd; background: transparent;")
        camera_layout.addWidget(title_label)
        
        video_label = VideoLabel()
        video_label.setMinimumSize(320, 240)
        camera_layout.addWidget(video_label)
        
        overlay_layout = QVBoxLayout(video_label)
        overlay_layout.setContentsMargins(15, 15, 15, 15)
        overlay_layout.setAlignment(Qt.AlignTop | Qt.AlignRight)
        
        overlay_label = QLabel()
        overlay_label.setStyleSheet("color: #ff3333; font-size: 24px; font-weight: bold; background: transparent;")
        overlay_label.setText("")
        overlay_layout.addWidget(overlay_label)
        
        if description == "主视图":
            self.cam_high_label = video_label
            self.cam_high_overlay = overlay_label
        else:
            self.cam_left_wrist_label = video_label
            self.cam_left_wrist_overlay = overlay_label
            
        return camera_widget
        
    def create_console_area(self):
        console_frame = QFrame()
        console_frame.setFrameShape(QFrame.Box)
        console_frame.setStyleSheet("background-color: #1e1e1e; color: white; border-top: 1px solid #444;")
        console_frame.setMinimumHeight(300)
        
        console_layout = QVBoxLayout(console_frame)
        console_layout.setSpacing(20)
        console_layout.setContentsMargins(30, 25, 30, 25)
        
        # --- 1. 机器人状态显示 (移到最上方 & 字号加大) ---
        self.robot_status_label = QLabel("Robot State: Waiting...")
        # 字体加大到 22px，加粗，使用等宽字体显示数字更整齐
        self.robot_status_label.setStyleSheet("color: #00e676; font-family: Monospace; font-size: 22px; font-weight: bold;")
        console_layout.addWidget(self.robot_status_label)

        # --- 2. 输入区域 Grid ---
        input_grid = QGridLayout()
        input_grid.setSpacing(15)
        
        label_font = QFont("Arial", 14, QFont.Bold)
        input_style = """
            QLineEdit {
                padding: 10px; font-size: 18px; color: black; background-color: #f0f0f0; 
                border-radius: 6px; border: 2px solid transparent;
            }
            QLineEdit:focus { border: 2px solid #2196F3; background-color: #ffffff; }
            QLineEdit:disabled { background-color: #cccccc; color: #555; }
        """

        # 任务描述
        desc_label = QLabel("任务描述:")
        desc_label.setFont(label_font)
        input_grid.addWidget(desc_label, 0, 0)

        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("例如: Pick up the red block")
        self.task_input.setStyleSheet(input_style)
        self.task_input.setFixedHeight(50)
        self.task_input.textChanged.connect(self.on_task_instruction_changed)
        input_grid.addWidget(self.task_input, 0, 1)
        
        self.lock_button = QPushButton("🔒 锁定")
        self.lock_button.setCheckable(True)
        self.lock_button.setFixedWidth(120)
        self.lock_button.setFixedHeight(50)
        self.lock_button.setStyleSheet("""
            QPushButton { font-size: 16px; background-color: #555; border-radius: 6px; color: white; font-weight: bold; }
            QPushButton:checked { background-color: #e65100; border: 2px solid #fff; }
        """)
        self.lock_button.toggled.connect(self.on_lock_toggled)
        input_grid.addWidget(self.lock_button, 0, 2)
        
        # 任务类型
        type_label = QLabel("任务类型:")
        type_label.setFont(label_font)
        input_grid.addWidget(type_label, 1, 0)
        
        self.type_input = QLineEdit()
        self.type_input.setPlaceholderText("例如: pick_place")
        self.type_input.setStyleSheet(input_style)
        self.type_input.setFixedHeight(50)
        self.type_input.textChanged.connect(self.on_task_type_changed)
        input_grid.addWidget(self.type_input, 1, 1)
        
        console_layout.addLayout(input_grid)
        
        # --- 3. 录制按钮 ---
        record_layout = QHBoxLayout()
        self.record_button = QPushButton("⏺ 开始录制")
        self.record_button.setCursor(Qt.PointingHandCursor)
        self.record_button.setFixedHeight(60)
        self.record_button.setStyleSheet("""
            QPushButton { padding: 10px 60px; font-size: 20px; background-color: #d32f2f; color: white; font-weight: bold; border-radius: 8px; }
            QPushButton:hover { background-color: #b71c1c; }
        """)
        self.record_button.clicked.connect(self.toggle_recording)
        
        record_layout.addStretch()
        record_layout.addWidget(self.record_button)
        record_layout.addStretch()
        
        console_layout.addLayout(record_layout)
        
        return console_frame
        
    def init_timers(self):
        self.record_blink_timer = QTimer()
        self.record_blink_timer.timeout.connect(self.blink_record_indicator)
        self.record_blink_timer.start(500)
        
    def start_threads(self):
        if TcpCameraServer:
            self.tcp_server = TcpCameraServer(port=8888)
            self.tcp_server.frame_ready.connect(self.on_frame_received)
            self.tcp_server.start()
        
        for cam_id in [0, 1]:
            try:
                cap = cv2.VideoCapture(cam_id)
                if cap.isOpened():
                    cap.release()
                    camera_name = "CAM_HIGH" if cam_id == 0 else "CAM_LEFT_WRIST"
                    local_thread = CameraThread(cam_id, f"Local_{camera_name}")
                    local_thread.frame_ready.connect(self.on_frame_received)
                    local_thread.start()
                    self.camera_threads[cam_id] = local_thread
            except Exception:
                pass
                
        self.robot_thread = MockRobotThread()
        self.robot_thread.robot_data_signal.connect(self.on_robot_data_received)
        self.robot_thread.start()
            
    def on_frame_received(self, camera_id: int, image: QImage):
        if camera_id == 0:
            self.update_camera_view(self.cam_high_label, image)
        elif camera_id == 1:
            self.update_camera_view(self.cam_left_wrist_label, image)
            
        if self.is_recording:
            self.save_frame(camera_id, image)
            
    def on_robot_data_received(self, timestamp: float, qpos: np.ndarray):
        qpos_str = ", ".join([f"{x:.2f}" for x in qpos])
        self.robot_status_label.setText(f"Robot (7-DOF): [{qpos_str}]")
        
        if self.is_recording:
            self.robot_data_buffer.append((timestamp, qpos.copy()))
                
    def update_camera_view(self, label: VideoLabel, image: QImage):
        label.set_image(image)
        
    def blink_record_indicator(self):
        if self.is_recording:
            if hasattr(self, 'blink_state'):
                self.blink_state = not self.blink_state
            else:
                self.blink_state = True
            indicator = "● REC" if self.blink_state else "○ REC"
            self.cam_high_overlay.setText(indicator)
            self.cam_left_wrist_overlay.setText(indicator)
        else:
            self.cam_high_overlay.setText("")
            self.cam_left_wrist_overlay.setText("")
            
    def on_task_instruction_changed(self, text: str):
        if not self.instruction_locked:
            self.task_instruction = text

    def on_task_type_changed(self, text: str):
        if not self.instruction_locked:
            self.task_type = text
            
    def on_lock_toggled(self, checked: bool):
        self.instruction_locked = checked
        self.task_input.setEnabled(not checked)
        self.type_input.setEnabled(not checked)
        
        if checked:
            self.lock_button.setText("🔓 解锁")
        else:
            self.lock_button.setText("🔒 锁定")
            
    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.record_start_time = time.time()
            self.frame_count = 0
            self.robot_data_buffer = []
            
            self.record_button.setText("⏹ 停止录制")
            self.record_button.setStyleSheet("""
                QPushButton { padding: 10px 60px; font-size: 20px; background-color: #388e3c; color: white; font-weight: bold; border-radius: 8px; }
                QPushButton:hover { background-color: #2e7d32; }
            """)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_record_dir = self.output_dir / timestamp
            self.current_record_dir.mkdir(exist_ok=True)
            self.video_writers = {}
            
            npz_path = self.current_record_dir / "task_info.npz"
            np.savez(str(npz_path), task_description=self.task_instruction, task_type=self.task_type)
            
            print(f"Started recording to {self.current_record_dir}")
            
        else:
            self.is_recording = False
            self.record_start_time = None
            
            self.record_button.setText("⏺ 开始录制")
            self.record_button.setStyleSheet("""
                QPushButton { padding: 10px 60px; font-size: 20px; background-color: #d32f2f; color: white; font-weight: bold; border-radius: 8px; }
                QPushButton:hover { background-color: #b71c1c; }
            """)
            
            if hasattr(self, 'video_writers'):
                for writer in self.video_writers.values():
                    writer.release()
                self.video_writers.clear()
            
            if self.robot_data_buffer:
                timestamps = np.array([x[0] for x in self.robot_data_buffer])
                qpos_data = np.array([x[1] for x in self.robot_data_buffer])
                robot_npz_path = self.current_record_dir / "robot_data.npz"
                np.savez(str(robot_npz_path), timestamps=timestamps, qpos=qpos_data)
                print(f"Robot data saved to {robot_npz_path}")
            
            print("Stopped recording")
            
    def save_frame(self, camera_id: int, image: QImage):
        camera_name = "cam_high" if camera_id == 0 else "cam_left_wrist"
        image = image.convertToFormat(QImage.Format_RGB888)
        width = image.width()
        height = image.height()
        
        ptr = image.bits()
        ptr.setsize(image.byteCount())
        arr = np.array(ptr).reshape(height, width, 3)
        frame_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        
        if camera_id not in self.video_writers:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            video_path = str(self.current_record_dir / f"{camera_name}.mp4")
            self.video_writers[camera_id] = cv2.VideoWriter(video_path, fourcc, 20.0, (width, height))
            
        self.video_writers[camera_id].write(frame_bgr)
        self.frame_count += 1

    def closeEvent(self, event):
        if self.tcp_server:
            self.tcp_server.stop()
            self.tcp_server.wait()
        if self.robot_thread:



            
            self.robot_thread.stop()
        for thread in self.camera_threads.values():
            if thread.isRunning():
                thread.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = DataCollectionApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
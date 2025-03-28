import sys
import os
import json
import uuid
import requests
import subprocess
import signal
from bs4 import BeautifulSoup
import html
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, 
                            QPushButton, QVBoxLayout, QHBoxLayout, 
                            QFileDialog, QMessageBox, QTableWidget, 
                            QTableWidgetItem, QHeaderView, QFrame,
                            QStyle, QStyleFactory, QSystemTrayIcon,
                            QDialog, QDateTimeEdit, QSpinBox, QDialogButtonBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QObject, QSize
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor, QPixmap
from queue import Queue
import logging
from datetime import datetime, timedelta
from io import BytesIO

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class StatusUpdateWorker(QObject):
    update_signal = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.queue = Queue()

    def process_updates(self):
        while not self.queue.empty():
            status, filename = self.queue.get()
            self.update_signal.emit(status, filename)

class StreamInfoExtractor:
    @staticmethod
    def extract_username_and_image(url):
        try:
            if url.startswith("view-source:"):
                url = url[12:]
            
            response = requests.get(url)
            if response.status_code != 200:
                raise Exception(f"Failed to retrieve the page: {url}")
            
            soup = BeautifulSoup(response.text, 'html.parser')
            og_description_tag = soup.find('meta', property='og:description')
            og_image_tag = soup.find('meta', property='og:image')
            
            if not og_description_tag:
                raise Exception("No 'og:description' meta tag found.")
            
            description_content = html.unescape(og_description_tag.get('content'))
            parts = description_content.split(" ")
            join_index = parts.index("Join")
            and_index = parts.index("&")
            
            user_parts = []
            for i in range(join_index + 1, and_index):
                if parts[i] != "&":
                    user_parts.append(parts[i])
            
            username = " ".join(user_parts).strip()
            image_url = og_image_tag.get('content') if og_image_tag else None
            
            return username, image_url
        except Exception as e:
            logging.error(f"Error extracting username from URL: {e}")
            raise

    @staticmethod
    def get_stream_info(url):
        try:
            username, image_url = StreamInfoExtractor.extract_username_and_image(url)
            folder_path = os.path.join(os.getcwd(), username)
            
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
            
            if image_url:
                try:
                    image_response = requests.get(image_url)
                    if image_response.status_code == 200:
                        image_filename = os.path.join(folder_path, f"{username}_profile.png")
                        with open(image_filename, 'wb') as f:
                            f.write(image_response.content)
                except Exception as e:
                    logging.error(f"Error downloading profile image: {e}")
            
            stream_id = url.split('/')[-1]
            
            return {
                'username': username,
                'folder_path': folder_path,
                'stream_id': stream_id
            }
        except Exception as e:
            logging.error(f"Error getting stream info: {e}")
            raise

class RecordingThread(QThread):
    update_status = pyqtSignal(str, str)
    update_duration = pyqtSignal(str, int)
    update_file_size = pyqtSignal(str, float)
    start_timer = pyqtSignal()
    stop_timer = pyqtSignal()
    
    def __init__(self, stream_url, output_file, max_duration=None, max_retries=3):
        super().__init__()
        self.stream_url = stream_url
        self.output_file = output_file
        self.max_duration = max_duration
        self.process = None
        self.stop_recording = False
        self.recording_duration = 0
        self.max_retries = max_retries
        self.retry_count = 0
        
        self.duration_timer = QTimer()
        self.duration_timer.timeout.connect(self.update_recording_duration)
        self.duration_timer.moveToThread(QApplication.instance().thread())
        
        self.start_timer.connect(self.duration_timer.start)
        self.stop_timer.connect(self.duration_timer.stop)

    def update_recording_duration(self):
        self.recording_duration += 1
        self.update_duration.emit(self.output_file, self.recording_duration)

    def run(self):
        while self.retry_count < self.max_retries and not self.stop_recording:
            try:
                self.recording_duration = 0
                self.start_timer.emit()
                
                ffmpeg_command = [
                    'ffmpeg',
                    '-hide_banner',
                    '-loglevel', 'panic',
                    '-i', self.stream_url,
                    '-c', 'copy',
                    '-vsync', '0',
                    '-copyts',
                    '-avoid_negative_ts', 'make_zero'
                ]

                if self.max_duration:
                    ffmpeg_command.extend(['-t', str(self.max_duration)])

                ffmpeg_command.extend([
                    '-fflags', '+genpts',
                    '-hls_time', '2',
                    '-hls_flags', 'split_by_time',
                    self.output_file
                ])

                self.process = subprocess.Popen(
                    ffmpeg_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )
                
                while self.process.poll() is None and not self.stop_recording:
                    self.msleep(100)
                    if os.path.exists(self.output_file):
                        size_mb = os.path.getsize(self.output_file) / (1024 * 1024)
                        self.update_file_size.emit(self.output_file, size_mb)
                
                if self.process and not self.stop_recording:
                    self.process.wait()
                    self.update_status.emit('completed', self.output_file)
                elif self.process:
                    if os.name == 'nt':
                        self.process.send_signal(signal.CTRL_C_EVENT)
                    else:
                        self.process.send_signal(signal.SIGINT)
                    
                    self.process.wait()
                    self.update_status.emit('stopped', self.output_file)
                
                if self.process.returncode != 0 and not self.stop_recording:
                    self.retry_count += 1
                    self.update_status.emit('reconnecting', self.output_file)
                    self.msleep(5000)
                    continue
                break
                
            except Exception as e:
                self.retry_count += 1
                logging.error(f"Error in RecordingThread (attempt {self.retry_count}): {e}")
                if self.retry_count >= self.max_retries:
                    self.update_status.emit('error', str(e))
                else:
                    self.update_status.emit('reconnecting', self.output_file)
                    self.msleep(5000)
            finally:
                self.stop_timer.emit()
                if self.process:
                    try:
                        self.process.terminate()
                    except:
                        pass

    def stop(self):
        self.stop_recording = True
        self.stop_timer.emit()
        if self.process:
            self.process.terminate()

class ScheduleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Schedule Recording')
        layout = QVBoxLayout()
        
        self.start_time = QDateTimeEdit(datetime.now())
        self.duration = QSpinBox()
        self.duration.setRange(1, 24*60)
        self.duration.setSuffix(' minutes')
        
        layout.addWidget(QLabel('Start Time:'))
        layout.addWidget(self.start_time)
        layout.addWidget(QLabel('Duration:'))
        layout.addWidget(self.duration)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setLayout(layout)

class TangoVideoRecorder(QWidget):
    def __init__(self):
        super().__init__()
        self.recordings = {}
        self.streams_file = 'stream_links.json'
        self.output_dir = ''
        self.status_worker = StatusUpdateWorker()
        self.status_worker.update_signal.connect(self.update_recording_status)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.status_worker.process_updates)
        self.update_timer.start(100)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.tray_icon.show()
        self.stats_file = 'recording_stats.json'
        self.stats = {
            'total_recordings': 0,
            'total_duration': 0,
            'total_size': 0
        }
        self.load_stats()
        self.initUI()
        self.load_streams()

    def initUI(self):
        self.setWindowTitle('mrr00tsuz')
        self.setGeometry(100, 100, 900, 600)
        self.setStyleSheet("""
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QLineEdit {
                padding: 8px;
                border: 1px solid #555555;
                border-radius: 4px;
                background-color: #363636;
                color: #ffffff;
            }
            QPushButton {
                padding: 8px 15px;
                background-color: #0d6efd;
                color: white;
                border: none;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #0b5ed7;
            }
            QPushButton:pressed {
                background-color: #0a58ca;
            }
            QPushButton:disabled {
                background-color: #6c757d;
            }
            QTableWidget {
                background-color: #363636;
                border: 1px solid #555555;
                border-radius: 4px;
                gridline-color: #555555;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QHeaderView::section {
                background-color: #404040;
                padding: 5px;
                border: 1px solid #555555;
                color: white;
            }
            QLabel {
                color: #ffffff;
                font-size: 12px;
            }
        """)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_label = QLabel('Tango live stream recorder')
        header_label.setStyleSheet("""
            font-size: 24px;
            color: #ffffff;
            padding: 10px;
        """)
        header_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(header_label)

        input_frame = QFrame()
        input_frame.setStyleSheet("""
            QFrame {
                background-color: #363636;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        input_layout = QVBoxLayout(input_frame)
        input_layout.setSpacing(10)

        url_layout = QHBoxLayout()
        self.url_label = QLabel('Stream URL:')
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText('Enter Tango stream URL here...')
        self.add_stream_button = QPushButton('Add Stream')
        self.add_stream_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        url_layout.addWidget(self.url_label)
        url_layout.addWidget(self.url_input, stretch=1)
        url_layout.addWidget(self.add_stream_button)
        input_layout.addLayout(url_layout)

        output_layout = QHBoxLayout()
        self.output_label = QLabel('Save Location:')
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText('Select output directory...')
        self.browse_button = QPushButton('Browse')
        self.browse_button.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(self.output_path, stretch=1)
        output_layout.addWidget(self.browse_button)
        input_layout.addLayout(output_layout)

        duration_layout = QHBoxLayout()
        self.duration_label = QLabel('Max Duration')
        self.duration_input = QLineEdit()
        self.duration_input.setPlaceholderText('Optional')
        duration_layout.addWidget(self.duration_label)
        duration_layout.addWidget(self.duration_input, stretch=1)
        input_layout.addLayout(duration_layout)

        main_layout.addWidget(input_frame)

        self.streams_table = QTableWidget()
        self.streams_table.setColumnCount(7)
        self.streams_table.setHorizontalHeaderLabels(['Profile', 'Stream URL', 'Status', 'Duration', 'Size (MB)', 'Recording', 'Actions'])
        
        self.streams_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.streams_table.setColumnWidth(0, 96)
        
        self.streams_table.verticalHeader().setDefaultSectionSize(96)
        
        for i in range(1, self.streams_table.columnCount()):
            self.streams_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        
        self.streams_table.setAlternatingRowColors(True)
        self.streams_table.setStyleSheet("""
            QTableWidget {
                background-color: #363636;
                alternate-background-color: #404040;
            }
            QTableWidget::item {
                color: white;
            }
            QLabel {
                background-color: transparent;
                padding: 2px;
            }
        """)
        main_layout.addWidget(self.streams_table)

        self.setLayout(main_layout)

        # Connect signals
        self.add_stream_button.clicked.connect(self.add_stream)
        self.browse_button.clicked.connect(self.browse_output_directory)

    def browse_output_directory(self):
        directory = QFileDialog.getExistingDirectory(self, 'Select Output Directory')
        if directory:
            self.output_dir = directory
            self.output_path.setText(directory)

    def format_duration(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def add_stream(self):
        stream_url = self.url_input.text().strip()

        if not stream_url:
            QMessageBox.warning(self, 'Error', 'Please enter a stream URL')
            return

        try:
            info = StreamInfoExtractor.get_stream_info(stream_url)
            
            self.output_dir = info['folder_path']
            self.output_path.setText(self.output_dir)
            
            api_url = f'http://localhost/tango.php?streamid={info["stream_id"]}'
            response = requests.get(api_url)
            data = response.json()

            valid_urls = []
            hd_url = None
            if isinstance(data.get('urls'), list):
                for url in data['urls']:
                    if (isinstance(url, str) and 
                        url.startswith('https://') and 
                        url.rstrip().endswith(tuple(str(i) for i in range(10)))):
                        valid_urls.append(url)
                        
                        if 'hd.m3u8' in url:
                            hd_url = url

            if not valid_urls:
                QMessageBox.warning(self, 'Error', 'No valid stream URL found')
                return

            hd_stream = hd_url if hd_url else valid_urls[0]

            random_filename = f'{info["username"]}_{uuid.uuid4()}.ts'
            output_file = os.path.join(self.output_dir, random_filename)

            max_duration = self.duration_input.text().strip() or None
            if max_duration:
                try:
                    max_duration = int(max_duration)
                except ValueError:
                    QMessageBox.warning(self, 'Error', 'Duration must be a number')
                    return

            recording_thread = RecordingThread(hd_stream, output_file, max_duration)
            recording_thread.update_status.connect(self.queue_status_update)
            recording_thread.update_duration.connect(self.update_duration)
            recording_thread.update_file_size.connect(self.update_file_size)
            recording_thread.start()

            profile_image = None
            try:
                image_path = os.path.join(info['folder_path'], f"{info['username']}_profile.png")
                if os.path.exists(image_path):
                    profile_image = QPixmap(image_path)
                    profile_image = profile_image.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception as e:
                logging.error(f"Error loading profile image: {e}")

            row = self.streams_table.rowCount()
            self.streams_table.insertRow(row)
            self.recordings[random_filename] = {
                'thread': recording_thread,
                'stream_url': stream_url,
                'hd_stream': hd_stream,
                'output_file': output_file,
                'row': row,
                'username': info['username'],
                'duration': 0
            }

            if profile_image:
                image_label = QLabel()
                image_label.setPixmap(profile_image)
                image_label.setAlignment(Qt.AlignCenter)
                self.streams_table.setCellWidget(row, 0, image_label)
                self.streams_table.setItem(row, 1, QTableWidgetItem(f"{info['username']} - {stream_url}"))
                self.streams_table.setItem(row, 2, QTableWidgetItem('Recording'))
                self.streams_table.setItem(row, 3, QTableWidgetItem('00:00'))
                self.streams_table.setItem(row, 4, QTableWidgetItem('0.0'))
            else:
                self.streams_table.setItem(row, 0, QTableWidgetItem(info['username']))
                self.streams_table.setItem(row, 1, QTableWidgetItem(stream_url))
                self.streams_table.setItem(row, 2, QTableWidgetItem('Recording'))
                self.streams_table.setItem(row, 3, QTableWidgetItem('00:00'))
                self.streams_table.setItem(row, 4, QTableWidgetItem('0.0'))
            
            stop_button = QPushButton('Stop')
            stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
            stop_button.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545;
                }
                QPushButton:hover {
                    background-color: #bb2d3b;
                }
                QPushButton:pressed {
                    background-color: #a52834;
                }
            """)
            stop_button.clicked.connect(lambda _, f=random_filename: self.stop_recording(f))
            self.streams_table.setCellWidget(row, 6, stop_button)

            # Save stream info
            self.save_streams()

        except Exception as e:
            logging.error(f"Error adding stream: {e}")
            QMessageBox.warning(self, 'Error', f'Failed to add stream: {str(e)}')

    def queue_status_update(self, status, filename):
        self.status_worker.queue.put((status, filename))

    def update_recording_status(self, status, filename):
        try:
            basename = os.path.basename(filename)
            if basename in self.recordings:
                row = self.recordings[basename]['row']
                if status in ['stopped', 'completed', 'error']:
                    self.streams_table.setItem(row, 1, QTableWidgetItem(status.capitalize()))
                    
                    if self.streams_table.cellWidget(row, 6):
                        self.streams_table.removeCellWidget(row, 6)
                    
                    if status == 'completed' and basename in self.recordings:
                        duration = self.recordings[basename]['duration']
                        size = 0
                        if os.path.exists(self.recordings[basename]['output_file']):
                            size = os.path.getsize(self.recordings[basename]['output_file']) / (1024 * 1024)
                        self.update_stats(duration, size)
                    
                    if basename in self.recordings:
                        del self.recordings[basename]
                else:
                    self.streams_table.setItem(row, 1, QTableWidgetItem(status.capitalize()))
                
                self.save_streams()
            
            if status in ['completed', 'error']:
                self.tray_icon.showMessage(
                    'Recording Status',
                    f'Recording {basename} has {status}',
                    QSystemTrayIcon.Information,
                    3000
                )
        except Exception as e:
            logging.error(f"Error updating recording status: {e}")
            QMessageBox.critical(self, 'Error', f"Status update failed: {str(e)}")

    def stop_recording(self, filename):
        try:
            recording = self.recordings.get(filename)
            if recording:
                if recording['thread'].isRunning():
                    recording['thread'].stop()
                    recording['thread'].wait()
                
                row = recording['row']
                self.streams_table.setItem(row, 1, QTableWidgetItem('Stopped'))
                
                if self.streams_table.cellWidget(row, 6):
                    self.streams_table.cellWidget(row, 6).setEnabled(False)
                
                self.queue_status_update('stopped', recording['output_file'])
                
        except Exception as e:
            logging.error(f"Error stopping recording: {e}")
            QMessageBox.critical(self, 'Error', f"Recording stop failed: {str(e)}")

    def save_streams(self):
        try:
            streams_data = []
            for filename, recording in self.recordings.items():
                streams_data.append({
                    'filename': filename,
                    'stream_url': recording['stream_url'],
                    'hd_stream': recording['hd_stream'],
                    'output_file': recording['output_file'],
                    'username': recording['username']
                })
            
            with open(self.streams_file, 'w') as f:
                json.dump(streams_data, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving streams: {e}")

    def load_streams(self):
        if os.path.exists(self.streams_file):
            try:
                with open(self.streams_file, 'r') as f:
                    streams_data = json.load(f)
                for stream in streams_data:
                    self.add_stream_from_data(stream)
            except Exception as e:
                logging.error(f"Error loading streams: {e}")

    def add_stream_from_data(self, stream_data):
        try:
            random_filename = stream_data['filename']
            output_file = stream_data['output_file']
            stream_url = stream_data['stream_url']
            
            urls = stream_data.get('urls', [])
            valid_urls = []
            hd_url = None
            if isinstance(urls, list):
                for url in urls:
                    if (isinstance(url, str) and 
                        url.startswith('https://') and 
                        url.rstrip().endswith(tuple(str(i) for i in range(10)))):
                        valid_urls.append(url)

                        if 'hd.m3u8' in url:
                            hd_url = url
            
            hd_stream = hd_url if hd_url else valid_urls[0] if valid_urls else stream_data['hd_stream']
            username = stream_data.get('username', 'Unknown')

            profile_image = None
            try:
                image_path = os.path.join(os.path.dirname(output_file), f"{username}_profile.png")
                if os.path.exists(image_path):
                    profile_image = QPixmap(image_path)
                    profile_image = profile_image.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception as e:
                logging.error(f"Error loading profile image: {e}")

            recording_thread = RecordingThread(hd_stream, output_file)
            recording_thread.update_status.connect(self.queue_status_update)
            recording_thread.update_duration.connect(self.update_duration)
            recording_thread.update_file_size.connect(self.update_file_size)

            row = self.streams_table.rowCount()
            self.streams_table.insertRow(row)
            self.recordings[random_filename] = {
                'thread': recording_thread,
                'stream_url': stream_url,
                'hd_stream': hd_stream,
                'output_file': output_file,
                'row': row,
                'username': username,
                'duration': 0
            }

            if profile_image:
                image_label = QLabel()
                image_label.setPixmap(profile_image)
                image_label.setAlignment(Qt.AlignCenter)
                self.streams_table.setCellWidget(row, 0, image_label)
                self.streams_table.setItem(row, 1, QTableWidgetItem(f"{username} - {stream_url}"))
                self.streams_table.setItem(row, 2, QTableWidgetItem('Recording'))
                self.streams_table.setItem(row, 3, QTableWidgetItem('00:00'))
                self.streams_table.setItem(row, 4, QTableWidgetItem('0.0'))
            else:
                self.streams_table.setItem(row, 0, QTableWidgetItem(username))
                self.streams_table.setItem(row, 1, QTableWidgetItem(stream_url))
                self.streams_table.setItem(row, 2, QTableWidgetItem('Recording'))
                self.streams_table.setItem(row, 3, QTableWidgetItem('00:00'))
                self.streams_table.setItem(row, 4, QTableWidgetItem('0.0'))
            
            stop_button = QPushButton('Stop')
            stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
            stop_button.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545;
                }
                QPushButton:hover {
                    background-color: #bb2d3b;
                }
                QPushButton:pressed {
                    background-color: #a52834;
                }
            """)
            stop_button.clicked.connect(lambda _, f=random_filename: self.stop_recording(f))
            self.streams_table.setCellWidget(row, 6, stop_button)

            recording_thread.start()

        except Exception as e:
            logging.error(f"Error adding stream from data: {e}")

    def update_duration(self, output_file, duration):
        try:
            basename = os.path.basename(output_file)
            if basename in self.recordings:
                row = self.recordings[basename]['row']
                self.recordings[basename]['duration'] = duration
                duration_text = self.format_duration(duration)
                self.streams_table.setItem(row, 2, QTableWidgetItem(duration_text))
        except Exception as e:
            logging.error(f"Error updating duration: {e}")

    def update_file_size(self, output_file, size_mb):
        try:
            basename = os.path.basename(output_file)
            if basename in self.recordings:
                row = self.recordings[basename]['row']
                self.streams_table.setItem(row, 4, QTableWidgetItem(f"{size_mb:.1f}"))
        except Exception as e:
            logging.error(f"Error updating file size: {e}")

    def stop_recording(self, filename):
        try:
            recording = self.recordings.get(filename)
            if recording:
                if recording['thread'].isRunning():
                    recording['thread'].stop()
                    recording['thread'].wait()
                
                row = recording['row']
                self.streams_table.setItem(row, 1, QTableWidgetItem('Stopped'))
                
                if self.streams_table.cellWidget(row, 6):
                    self.streams_table.cellWidget(row, 6).setEnabled(False)
                
                self.queue_status_update('stopped', recording['output_file'])
                
        except Exception as e:
            logging.error(f"Error stopping recording: {e}")
            QMessageBox.critical(self, 'Error', f"Recording stop failed: {str(e)}")

    def closeEvent(self, event):
        try:
            for filename, recording in list(self.recordings.items()):
                if recording['thread'].isRunning():
                    self.stop_recording(filename)
                    recording['thread'].wait()
            
            for recording in self.recordings.values():
                if recording['thread'].isRunning():
                    recording['thread'].wait()
            
            self.update_timer.stop()
            
            self.tray_icon.hide()
            
            event.accept()
        except Exception as e:
            logging.error(f"Error during close: {e}")
            event.accept()

    def schedule_recording(self):
        dialog = ScheduleDialog(self)
        if dialog.exec_():
            start_time = dialog.start_time.dateTime().toPyDateTime()
            duration = dialog.duration.value()
            
            delay = (start_time - datetime.now()).total_seconds()
            if delay > 0:
                QTimer.singleShot(int(delay * 1000), 
                                lambda: self.start_scheduled_recording(duration))

    def start_scheduled_recording(self, duration):
        self.duration_input.setText(str(duration * 60))
        self.add_stream()

    def update_stats(self, duration, size):
        self.stats['total_recordings'] += 1
        self.stats['total_duration'] += duration
        self.stats['total_size'] += size
        self.save_stats()

    def show_stats(self):
        msg = QMessageBox()
        msg.setWindowTitle('Recording Statistics')
        msg.setText(f"""
        Total Recordings: {self.stats['total_recordings']}
        Total Duration: {self.format_duration(self.stats['total_duration'])}
        Total Size: {self.stats['total_size'] / 1024:.1f} GB
        """)
        msg.exec_()

    def load_stats(self):
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    self.stats = json.load(f)
            else:
                self.stats = {
                    'total_recordings': 0,
                    'total_duration': 0,
                    'total_size': 0
                }
        except Exception as e:
            logging.error(f"Error loading stats: {e}")
            self.stats = {
                'total_recordings': 0,
                'total_duration': 0,
                'total_size': 0
            }

    def save_stats(self):
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving stats: {e}")

def main():
    app = QApplication(sys.argv)
    recorder = TangoVideoRecorder()
    recorder.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Application terminated by user.")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")

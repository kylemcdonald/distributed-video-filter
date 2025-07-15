import cv2
import numpy as np
import pyglet
from pyglet.gl import *
import threading
import time
import queue
import zmq
import sys
import json
import signal
import os
from perfetto import trace_processor
from perfetto.trace_processor import TraceProcessor
import tempfile
import shutil

# Constants
CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
TARGET_SIZE = 480
camera_fps = 30  # Increased from 15

class WebcamApp:
    def __init__(self, distribute_port=5555, collect_port=5556):
        self.window = pyglet.window.Window(
            width=TARGET_SIZE * 2,  # Double width to accommodate both frames
            height=TARGET_SIZE, 
            caption='Webcam Feed with Inverted Frame'
        )
        self.frame_data = None
        self.texture = None
        self.running = False
        self.cap = None
        
        # Texture management
        self.inverted_texture = None
        
        # Frame processing queue - increased size and added overflow tracking
        self.frame_queue = queue.Queue(maxsize=20)  # Increased from 10 to 20
        
        # Frame index tracking
        self.frame_index = 0
        
        # Track the last frame sent to any client to prevent duplicates
        self.last_frame_sent = -1
        
        # Frame drop tracking
        self.frames_dropped = 0
        self.frames_processed = 0
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # ROUTER socket to handle client requests and send frames
        self.distribute_socket = self.context.socket(zmq.ROUTER)
        self.distribute_socket.bind(f"tcp://*:{distribute_port}")
        
        # PULL socket to receive inverted frames from inverter
        self.collect_socket = self.context.socket(zmq.PULL)
        self.collect_socket.bind(f"tcp://*:{collect_port}")
        
        # Frame rate monitoring
        self.capture_fps_counter = 0
        self.capture_fps_start_time = time.time()
        self.draw_fps_counter = 0
        self.draw_fps_start_time = time.time()
        
        # Perfetto trace configuration
        self.trace_config = {
            'buffers': [{'size_kb': 1024}],
            'data_sources': [{
                'config': {
                    'name': 'org.chromium.trace_event',
                    'chrome_trace_config': {
                        'trace_config': {
                            'included_categories': ['*'],
                            'excluded_categories': [],
                            'record_mode': 'record_continuously'
                        }
                    }
                }
            }]
        }
        
        # Frame timing tracking
        self.frame_timings = []
        self.trace_start_time = time.time()
        
        # Set up signal handlers for Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Set up event handlers
        self.window.on_draw = self.on_draw
        self.window.on_key_press = self.on_key_press
        
        # Start frame reading thread
        self.running = True
        self.frame_thread = threading.Thread(target=self.read_frames)
        self.frame_thread.daemon = True
        self.frame_thread.start()
        
        # Start processing thread
        self.processing_thread = threading.Thread(target=self.process_frames)
        self.processing_thread.daemon = True
        self.processing_thread.start()
        
        # Start distribute handling thread
        self.distribute_thread = threading.Thread(target=self.handle_distribute_requests)
        self.distribute_thread.daemon = True
        self.distribute_thread.start()
        
        # Start inverter output checking thread
        self.inverter_thread = threading.Thread(target=self.check_inverter_output)
        self.inverter_thread.daemon = True
        self.inverter_thread.start()
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C and export Perfetto trace"""
        print(f"\nReceived signal {signum}, exporting Perfetto trace...")
        self.export_perfetto_trace()
        self.cleanup()
        pyglet.app.exit()
    
    def log_frame_timing(self, frame_index, timestamp, event_type="frame_captured"):
        """Log frame timing event for Perfetto trace"""
        self.frame_timings.append({
            'frame_index': frame_index,
            'timestamp': timestamp,
            'event_type': event_type,
            'relative_time': timestamp - self.trace_start_time,
            'event_ph': 'i'  # Instant event
        })
    
    def log_frame_complete_timing(self, frame_index, begin_time, end_time, event_type="frame_processed", pid=None):
        """Log frame timing as complete event with duration"""
        self.frame_timings.append({
            'frame_index': frame_index,
            'begin_time': begin_time,
            'end_time': end_time,
            'event_type': event_type,
            'begin_relative_time': begin_time - self.trace_start_time,
            'end_relative_time': end_time - self.trace_start_time,
            'event_ph': 'X',  # Complete event
            'pid': pid
        })
    
    def export_perfetto_trace(self):
        """Export frame timing data to Perfetto trace format"""
        if not self.frame_timings:
            print("No frame timing data to export")
            return
        
        # Create trace file
        trace_file = "webcam_frame_timing.pftrace"
        
        # Write trace data in Perfetto format
        with open(trace_file, 'w') as f:
            f.write('{\n')
            f.write('  "traceEvents": [\n')
            
            for i, timing in enumerate(self.frame_timings):
                if timing['event_ph'] == 'i':  # Instant event
                    event = {
                        "name": f"Frame {timing['frame_index']} - {timing['event_type']}",
                        "cat": "video_frames",
                        "ph": "i",  # Instant event
                        "ts": int(timing['relative_time'] * 1000000),  # Convert to microseconds
                        "pid": os.getpid(),
                        "tid": threading.get_ident(),
                        "args": {
                            "frame_index": timing['frame_index'],
                            "event_type": timing['event_type'],
                            "absolute_timestamp": timing['timestamp']
                        }
                    }
                else:  # Complete event
                    duration = timing['end_relative_time'] - timing['begin_relative_time']
                    event = {
                        "name": f"Frame {timing['frame_index']} - {timing['event_type']}",
                        "cat": "video_frames",
                        "ph": "X",  # Complete event
                        "ts": int(timing['begin_relative_time'] * 1000000),  # Convert to microseconds
                        "dur": int(duration * 1000000),  # Duration in microseconds
                        "pid": timing.get('pid', os.getpid()),
                        "tid": threading.get_ident(),
                        "args": {
                            "frame_index": timing['frame_index'],
                            "event_type": timing['event_type'],
                            "begin_timestamp": timing['begin_time'],
                            "end_timestamp": timing['end_time'],
                            "duration_ms": duration * 1000
                        }
                    }
                
                f.write('    ' + json.dumps(event))
                if i < len(self.frame_timings) - 1:
                    f.write(',')
                f.write('\n')
            
            f.write('  ]\n')
            f.write('}\n')
        
        print(f"Perfetto trace exported to: {trace_file}")
        print(f"Total frames logged: {len(self.frame_timings)}")
        
        # Print timing statistics
        if self.frame_timings:
            # Separate instant and complete events for statistics
            instant_events = [t for t in self.frame_timings if t['event_ph'] == 'i']
            complete_events = [t for t in self.frame_timings if t['event_ph'] == 'X']
            
            if instant_events:
                timestamps = [t['timestamp'] for t in instant_events]
                intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    print(f"Average frame capture interval: {avg_interval*1000:.2f}ms")
                    print(f"Frame capture rate: {1/avg_interval:.1f} FPS")
            
            if complete_events:
                durations = [(t['end_time'] - t['begin_time']) for t in complete_events]
                if durations:
                    avg_duration = sum(durations) / len(durations)
                    print(f"Average processing duration: {avg_duration*1000:.2f}ms")
                    print(f"Processing rate: {1/avg_duration:.1f} FPS")
                    print(f"Total frames processed: {len(complete_events)}")
    
    def read_frames(self):
        print("Starting OpenCV video capture...")
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, camera_fps)
        
        # Set buffer size to minimize latency
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not self.cap.isOpened():
            print("Error: Could not open camera")
            self.running = False
            return
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                print("Error: Can't receive frame")
                break
            
            # Log frame timing for Perfetto trace
            current_time = time.time()
            self.log_frame_timing(self.frame_index, current_time, "frame_captured")
            
            # Update capture FPS counter
            self.capture_fps_counter += 1
            if current_time - self.capture_fps_start_time >= 5.0:
                capture_fps = self.capture_fps_counter / (current_time - self.capture_fps_start_time)
                print(f"Capture FPS: {capture_fps:.1f}")
                self.capture_fps_counter = 0
                self.capture_fps_start_time = current_time
            
            # Add frame to processing queue with metadata (non-blocking)
            try:
                frame_data = {
                    'frame': frame,
                    'frame_index': self.frame_index,
                    'timestamp': current_time
                }
                self.frame_queue.put_nowait(frame_data)
                
                # Increment frame index for next frame
                self.frame_index += 1
            except queue.Full:
                # Try to clear old frames and retry once
                try:
                    # Remove oldest frame to make room
                    _ = self.frame_queue.get_nowait()
                    # Retry putting the new frame
                    self.frame_queue.put_nowait(frame_data)
                    self.frame_index += 1
                    print(f"Replaced old frame with new frame {self.frame_index-1}")
                except queue.Full:
                    # Still full, drop the frame
                    self.frames_dropped += 1
                    print(f"Frame {self.frame_index} dropped due to queue overflow. Total dropped: {self.frames_dropped}")
                    self.frame_index += 1
        
        self.cap.release()
        print("Camera released.")
    
    def process_frames(self):
        """
        Process frames in a separate thread to avoid blocking capture.

        Frame dropping can occur here: if a frame is removed from the queue but never sent to a client,
        it is effectively dropped. This happens when the output from this function (to global state)
        is not consumed as quickly as the camera produces frames.
        """
        while self.running:
            try:
                # Use shorter timeout to be more responsive
                frame_data = self.frame_queue.get(timeout=0.05)
                
                # Flip the frame vertically to fix upside-down issue
                frame = cv2.flip(frame_data['frame'], 0)
                
                # Center crop to TARGET_SIZE x TARGET_SIZE
                h, w, _ = frame.shape
                crop_x = (w - TARGET_SIZE) // 2
                crop_y = (h - TARGET_SIZE) // 2
                frame_cropped = frame[crop_y:crop_y+TARGET_SIZE, crop_x:crop_x+TARGET_SIZE]
                
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame_cropped, cv2.COLOR_BGR2RGB)
                
                # Store processed frame data with metadata
                self.frame_data = {
                    'frame': frame_rgb,
                    'frame_index': frame_data['frame_index']
                }
                
                # Schedule texture updates less frequently
                pyglet.clock.schedule_once(self.update_textures, 0)
                
                self.frames_processed += 1
                
            except queue.Empty:
                # Shorter sleep when no frames available
                time.sleep(0.001)
                continue
            except Exception as e:
                print(f"Error processing frame: {e}")
                time.sleep(0.001)
                continue
    
    def handle_distribute_requests(self):
        """Handle client requests for frames via ROUTER socket"""
        while self.running:
            try:
                # Poll for messages from clients with shorter timeout
                if self.distribute_socket.poll(10):  # 5ms timeout for more responsiveness
                    # Receive client identity and message
                    client_id = self.distribute_socket.recv(zmq.NOBLOCK)
                    message = self.distribute_socket.recv_string(zmq.NOBLOCK)
                    
                    if message == "READY":
                        # Client is ready for a frame
                        if self.frame_data is not None:
                            # Check if this is a new frame that hasn't been sent yet
                            if self.frame_data['frame_index'] > self.last_frame_sent:
                                try:
                                    # Send frame index and frame data to client
                                    self.distribute_socket.send(client_id, zmq.SNDMORE)
                                    self.distribute_socket.send_string(str(self.frame_data['frame_index']), zmq.SNDMORE)
                                    self.distribute_socket.send(self.frame_data['frame'].tobytes(), zmq.NOBLOCK)
                                    
                                    # Update tracking
                                    self.last_frame_sent = self.frame_data['frame_index']
                                    
                                    print(f"Sent frame {self.frame_data['frame_index']} to client")
                                except zmq.Again:
                                    print(f"Failed to send frame {self.frame_data['frame_index']} - socket buffer full")
                            # else:
                            #     print(f"Frame {self.frame_data['frame_index']} already sent, skipping")
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error handling distribute request: {e}")
                continue
    
    def check_inverter_output(self):
        """Check for inverted frames from inverter output queue and update texture"""
        while self.running:
            try:
                # Poll for messages with shorter timeout
                if self.collect_socket.poll(10):  # 10ms timeout for more responsiveness
                    # Receive inverted frame from inverter
                    frame_index = self.collect_socket.recv_string(zmq.NOBLOCK)
                    process_id = self.collect_socket.recv_string(zmq.NOBLOCK)
                    start_time = self.collect_socket.recv_string(zmq.NOBLOCK)
                    end_time = self.collect_socket.recv_string(zmq.NOBLOCK)
                    inverted_data = self.collect_socket.recv(zmq.NOBLOCK)
                    
                    # Log frame timing as complete event with duration
                    self.log_frame_complete_timing(int(frame_index), float(start_time), float(end_time), "frame_inverted_received", int(process_id))
                    
                    # Print frame index and process ID
                    processing_time = float(end_time) - float(start_time)                
                    print(f"Received frame {frame_index} from process {process_id} in {processing_time*1000:.0f}ms")
                    
                    # Convert bytes back to numpy array
                    inverted_frame = np.frombuffer(inverted_data, dtype=np.uint8).reshape(TARGET_SIZE, TARGET_SIZE, 3)
                    
                    # Update inverted texture on main thread
                    pyglet.clock.schedule_once(lambda dt: self.update_inverted_texture(inverted_frame), 0)
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error receiving inverted frame: {e}")
                continue
    
    def update_inverted_texture(self, inverted_frame):
        """Update the inverted texture with new frame data"""
        if inverted_frame is not None:
            image_data = pyglet.image.ImageData(
                TARGET_SIZE, TARGET_SIZE, 'RGB', 
                inverted_frame.tobytes()
            )
            self.inverted_texture = image_data.get_texture()
    
    def update_textures(self, dt):
        if self.frame_data is not None:
            image_data = pyglet.image.ImageData(
                TARGET_SIZE, TARGET_SIZE, 'RGB', 
                self.frame_data['frame'].tobytes()
            )
            self.texture = image_data.get_texture()
    
    def on_draw(self):
        self.window.clear()
        
        # Draw live feed on left half
        if self.texture:
            self.texture.blit(0, 0, width=TARGET_SIZE, height=TARGET_SIZE)
        
        # Draw inverted frame on right half
        if self.inverted_texture:
            self.inverted_texture.blit(TARGET_SIZE, 0, width=TARGET_SIZE, height=TARGET_SIZE)
        
        # Update draw FPS counter
        self.draw_fps_counter += 1
        current_time = time.time()
        if current_time - self.draw_fps_start_time >= 5.0:
            draw_fps = self.draw_fps_counter / (current_time - self.draw_fps_start_time)
            print(f"Draw FPS: {draw_fps:.1f}")
            self.draw_fps_counter = 0
            self.draw_fps_start_time = current_time
    
    def on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            print("\nESC pressed, exporting Perfetto trace...")
            self.export_perfetto_trace()
            self.cleanup()
            pyglet.app.exit()
    
    def cleanup(self):
        self.running = False
        if self.cap is not None:
            self.cap.release()
            print("Camera released.")
        
        # Close ZeroMQ sockets
        self.distribute_socket.close()
        self.collect_socket.close()
        self.context.term()
        print("ZeroMQ connections closed")
        
        # Print frame statistics
        total_frames = self.frames_processed + self.frames_dropped
        if total_frames > 0:
            drop_rate = (self.frames_dropped / total_frames) * 100
            print(f"Frame statistics:")
            print(f"  Total frames captured: {total_frames}")
            print(f"  Frames processed: {self.frames_processed}")
            print(f"  Frames dropped: {self.frames_dropped}")
            print(f"  Drop rate: {drop_rate:.1f}%")
        
        # Export trace if not already done
        if self.frame_timings:
            print("Exporting Perfetto trace on cleanup...")
            self.export_perfetto_trace()
    
    def run(self):
        print("Starting webcam application...")
        print("Press 'ESC' to quit")
        pyglet.app.run()

def main():
    # Parse command line arguments for ports
    distribute_port = 5555
    collect_port = 5556
    
    if len(sys.argv) >= 3:
        distribute_port = int(sys.argv[1])
        collect_port = int(sys.argv[2])
    
    app = WebcamApp(distribute_port, collect_port)
    app.run()

if __name__ == "__main__":
    main() 
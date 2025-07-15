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

# Constants
CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
TARGET_SIZE = 480
camera_fps = 30  # Increased from 15

class WebcamApp:
    def __init__(self, inverter_push_port=5555, inverter_pull_port=5556):
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
        
        # Frame processing queue
        self.frame_queue = queue.Queue(maxsize=2)
        
        # Frame index tracking
        self.frame_index = 0
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # PUSH socket to send frames to inverter
        self.push_socket = self.context.socket(zmq.PUSH)
        self.push_socket.bind(f"tcp://*:{inverter_push_port}")
        
        # PULL socket to receive inverted frames from inverter
        self.pull_socket = self.context.socket(zmq.PULL)
        self.pull_socket.bind(f"tcp://*:{inverter_pull_port}")
        
        # Frame rate monitoring
        self.capture_fps_counter = 0
        self.capture_fps_start_time = time.time()
        self.draw_fps_counter = 0
        self.draw_fps_start_time = time.time()
        
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
        
        # Start inverter output checking thread
        self.inverter_thread = threading.Thread(target=self.check_inverter_output)
        self.inverter_thread.daemon = True
        self.inverter_thread.start()
    
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
            
            # Update capture FPS counter
            self.capture_fps_counter += 1
            current_time = time.time()
            if current_time - self.capture_fps_start_time >= 5.0:
                capture_fps = self.capture_fps_counter / (current_time - self.capture_fps_start_time)
                print(f"Capture FPS: {capture_fps:.1f}")
                self.capture_fps_counter = 0
                self.capture_fps_start_time = current_time
            
            # Add frame to processing queue (non-blocking)
            try:
                self.frame_queue.put_nowait(frame)
            except queue.Full:
                # Skip frame if queue is full
                pass
        
        self.cap.release()
        print("Camera released.")
    
    def process_frames(self):
        """Process frames in a separate thread to avoid blocking capture"""
        while self.running:
            try:
                frame = self.frame_queue.get(timeout=0.1)
                
                # Flip the frame vertically to fix upside-down issue
                frame = cv2.flip(frame, 0)
                
                # Center crop to TARGET_SIZE x TARGET_SIZE
                h, w, _ = frame.shape
                crop_x = (w - TARGET_SIZE) // 2
                crop_y = (h - TARGET_SIZE) // 2
                frame_cropped = frame[crop_y:crop_y+TARGET_SIZE, crop_x:crop_x+TARGET_SIZE]
                
                # Convert BGR to RGB
                frame_rgb = cv2.cvtColor(frame_cropped, cv2.COLOR_BGR2RGB)
                self.frame_data = frame_rgb
                
                # Increment frame index
                self.frame_index += 1
                
                # Send frame and frame index to inverter via ZeroMQ
                try:
                    # Send frame index and frame data as multi-part message
                    self.push_socket.send_string(str(self.frame_index), zmq.SNDMORE)
                    self.push_socket.send(frame_rgb.tobytes(), zmq.NOBLOCK)
                except zmq.Again:
                    # Skip if socket is not ready
                    pass
                
                # Schedule texture updates less frequently
                pyglet.clock.schedule_once(self.update_textures, 0)
                
            except queue.Empty:
                continue
    
    def check_inverter_output(self):
        """Check for inverted frames from inverter output queue and update texture"""
        while self.running:
            try:
                # Receive inverted frame from inverter
                frame_index = self.pull_socket.recv_string(zmq.NOBLOCK)
                process_id = self.pull_socket.recv_string(zmq.NOBLOCK)
                inverted_data = self.pull_socket.recv(zmq.NOBLOCK)
                
                # Print frame index and process ID
                print(f"Received frame {frame_index} from process {process_id}")
                
                # Convert bytes back to numpy array
                inverted_frame = np.frombuffer(inverted_data, dtype=np.uint8).reshape(TARGET_SIZE, TARGET_SIZE, 3)
                
                # Update inverted texture on main thread
                pyglet.clock.schedule_once(lambda dt: self.update_inverted_texture(inverted_frame), 0)
                
            except zmq.Again:
                # No message available, continue
                time.sleep(0.01)
                continue
            except Exception as e:
                print(f"Error receiving inverted frame: {e}")
                time.sleep(0.01)
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
                self.frame_data.tobytes()
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
            self.cleanup()
            pyglet.app.exit()
    
    def cleanup(self):
        self.running = False
        if self.cap is not None:
            self.cap.release()
            print("Camera released.")
        
        # Close ZeroMQ sockets
        self.push_socket.close()
        self.pull_socket.close()
        self.context.term()
        print("ZeroMQ connections closed")
    
    def run(self):
        print("Starting webcam application...")
        print("Press 'ESC' to quit")
        pyglet.app.run()

def main():
    # Parse command line arguments for ports
    inverter_push_port = 5555
    inverter_pull_port = 5556
    
    if len(sys.argv) >= 3:
        inverter_push_port = int(sys.argv[1])
        inverter_pull_port = int(sys.argv[2])
    
    app = WebcamApp(inverter_push_port, inverter_pull_port)
    app.run()

if __name__ == "__main__":
    main() 
import cv2
import numpy as np
import pyglet
from pyglet.gl import *
import threading
import time
import signal
import sys
from distributor import Distributor

# Constants
CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
TARGET_SIZE = 480
camera_fps = 30  # Increased from 15

class WebcamApp(Distributor):
    def __init__(self, distribute_port=5555, collect_port=5556, frame_delay=5, target_size=480):
        # Initialize parent Distributor class with configurable frame delay
        super().__init__(distribute_port, collect_port, frame_delay)
        
        # Store target size for frame processing
        self.target_size = target_size
        
        # Pyglet window setup
        self.window = pyglet.window.Window(
            width=self.target_size * 2,  # Double width to accommodate both frames
            height=self.target_size, 
            caption='Webcam Feed with Inverted Frame'
        )
        
        # Camera and frame data
        self.frame_data = None
        self.texture = None
        self.inverted_texture = None
        self.cap = None
        
        # Frame rate monitoring
        self.capture_fps_counter = 0
        self.capture_fps_start_time = time.time()
        self.draw_fps_counter = 0
        self.draw_fps_start_time = time.time()
        
        # Frame index for camera capture
        self.camera_frame_index = 0
        
        # Set up signal handlers for Ctrl+C
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Set up event handlers
        self.window.on_draw = self.on_draw
        self.window.on_key_press = self.on_key_press
        
        # Start distributor threads
        self.start()
        
        # Start frame reading thread
        self.frame_thread = threading.Thread(target=self.read_frames)
        self.frame_thread.daemon = True
        self.frame_thread.start()
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C and export Perfetto trace"""
        print(f"\nReceived signal {signum}, exporting Perfetto trace...")
        self.export_perfetto_trace()
        self.cleanup()
        pyglet.app.exit()
    
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
            current_time = time.time()
            self.capture_fps_counter += 1
            if current_time - self.capture_fps_start_time >= 5.0:
                capture_fps = self.capture_fps_counter / (current_time - self.capture_fps_start_time)
                print(f"Capture FPS: {capture_fps:.1f}")
                self.capture_fps_counter = 0
                self.capture_fps_start_time = current_time
            
            # Process frame for display
            # Flip the frame vertically to fix upside-down issue
            frame = cv2.flip(frame, 0)
            
            # Center crop to target_size x target_size
            h, w, _ = frame.shape
            crop_x = (w - self.target_size) // 2
            crop_y = (h - self.target_size) // 2
            frame_cropped = frame[crop_y:crop_y+self.target_size, crop_x:crop_x+self.target_size]
            
            # Convert BGR to RGB
            frame_rgb = cv2.cvtColor(frame_cropped, cv2.COLOR_BGR2RGB)
            
            # Store processed frame data for display
            self.frame_data = {
                'frame': frame_rgb,
                'frame_index': self.camera_frame_index
            }
            
            # Send frame to distributor for distribution to workers
            self.add_frame_for_distribution(frame_rgb, self.camera_frame_index, current_time)
            
            # Increment frame index for next frame
            self.camera_frame_index += 1
            
            # Schedule texture updates less frequently
            pyglet.clock.schedule_once(self.update_textures, 0)
        
        self.cap.release()
        print("Camera released.")
    
    def reshape_frame_data(self, frame_data_bytes):
        """Reshape raw frame data bytes to numpy array"""
        if frame_data_bytes is not None:
            return np.frombuffer(frame_data_bytes, dtype=np.uint8).reshape(self.target_size, self.target_size, 3)
        return None
    
    def update_inverted_texture(self, inverted_frame):
        """Update the inverted texture with new frame data"""
        if inverted_frame is not None:
            image_data = pyglet.image.ImageData(
                self.target_size, self.target_size, 'RGB', 
                inverted_frame.tobytes()
            )
            self.inverted_texture = image_data.get_texture()
    
    def update_textures(self, dt):
        if self.frame_data is not None:
            image_data = pyglet.image.ImageData(
                self.target_size, self.target_size, 'RGB', 
                self.frame_data['frame'].tobytes()
            )
            self.texture = image_data.get_texture()
    
    def on_draw(self):
        self.window.clear()
        
        # Draw live feed on left half
        if self.texture:
            self.texture.blit(0, 0, width=self.target_size, height=self.target_size)
        
        # Update display frame and get frame to show
        frame_updated = self.update_display_frame()
        frame_data_bytes = self.get_frame_to_display()
        
        # Draw inverted frame on right half
        if frame_data_bytes is not None:
            # Reshape the raw bytes to numpy array
            frame_to_display = self.reshape_frame_data(frame_data_bytes)
            
            if frame_to_display is not None:
                # Update texture with the frame to display
                image_data = pyglet.image.ImageData(
                    self.target_size, self.target_size, 'RGB', 
                    frame_to_display.tobytes()
                )
                self.inverted_texture = image_data.get_texture()
                
                # Draw the frame
                self.inverted_texture.blit(self.target_size, 0, width=self.target_size, height=self.target_size)
            
        # Update draw FPS counter
        self.draw_fps_counter += 1
        current_time = time.time()
        if current_time - self.draw_fps_start_time >= 5.0:
            draw_fps = self.draw_fps_counter / (current_time - self.draw_fps_start_time)
            print(f"Draw FPS: {draw_fps:.1f}")
            
            # Get frame statistics from distributor
            stats = self.get_frame_stats()
            print(f"Frame buffer: {stats['buffer_size']} frames, current display: {stats['current_display_frame']}, latest received: {stats['latest_received_frame']}")
            
            self.draw_fps_counter = 0
            self.draw_fps_start_time = current_time
    
    def on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            print("\nESC pressed, exporting Perfetto trace...")
            self.export_perfetto_trace()
            self.cleanup()
            pyglet.app.exit()
    
    def cleanup(self):
        """Clean up camera and call parent cleanup"""
        self.running = False
        if self.cap is not None:
            self.cap.release()
            print("Camera released.")
        
        # Call parent cleanup for ZeroMQ connections
        super().cleanup()
    
    def run(self):
        print("Starting webcam application...")
        print("Press 'ESC' to quit")
        pyglet.app.run()

def main():
    # Parse command line arguments for ports and configuration
    distribute_port = 5555
    collect_port = 5556
    frame_delay = 5
    target_size = 480
    
    if len(sys.argv) >= 3:
        distribute_port = int(sys.argv[1])
        collect_port = int(sys.argv[2])
    
    if len(sys.argv) >= 4:
        frame_delay = int(sys.argv[3])
    
    if len(sys.argv) >= 5:
        target_size = int(sys.argv[4])
    
    app = WebcamApp(distribute_port, collect_port, frame_delay, target_size)
    app.run()

if __name__ == "__main__":
    main() 
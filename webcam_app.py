import cv2
import pyglet
from pyglet.gl import *
import threading
import time
import signal
import argparse
from distributor import Distributor

# Constants
CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
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
            caption='Webcam Feed'
        )
        
        # Camera and frame data
        self.frame_data = None
        self.texture = None
        self.processed_texture = None
        self.cap = None
        self.new_frame_available = False
        
        # Frame rate monitoring
        self.capture_fps_counter = 0
        self.capture_fps_start_time = time.time()
        self.draw_fps_counter = 0
        self.draw_fps_start_time = time.time()
        
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
        print(f"\nReceived signal {signum}")
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
            
            # Center crop to target_size x target_size
            h, w, _ = frame.shape
            crop_x = (w - self.target_size) // 2
            crop_y = (h - self.target_size) // 2
            frame_cropped = frame[crop_y:crop_y+self.target_size, crop_x:crop_x+self.target_size]
            frame_rgb = cv2.cvtColor(frame_cropped, cv2.COLOR_BGR2RGB)
            
            # Store processed frame data for display
            self.frame_data = frame_rgb
            self.new_frame_available = True
            
            # Send frame to distributor for distribution to workers
            self.add_frame_for_distribution(frame_rgb, current_time)
        
        self.cap.release()
        print("Camera released.")
    
    def on_draw(self):
        self.window.clear()
        
        # Update live feed texture if we have new frame data
        if self.frame_data is not None and self.new_frame_available:
            image_data = pyglet.image.ImageData(
                self.target_size, self.target_size, 'RGB', 
                self.frame_data.tobytes()
            )
            self.texture = image_data.get_texture().get_transform(flip_y=True, flip_x=True)
            self.texture.anchor_x = 0
            self.texture.anchor_y = 0
            self.new_frame_available = False
        
        if self.texture:
            self.texture.blit(0, 0, width=self.target_size, height=self.target_size)
        
        frame_updated = self.update_display_frame()
        if frame_updated:
            frame_data_bytes = self.get_frame_to_display()
            if frame_data_bytes is not None:
                image_data = pyglet.image.ImageData(
                    self.target_size, self.target_size, 'RGB', 
                    frame_data_bytes
                )
                self.processed_texture = image_data.get_texture().get_transform(flip_y=True, flip_x=True)
                self.processed_texture.anchor_x = 0
                self.processed_texture.anchor_y = 0
            
        if self.processed_texture:
            self.processed_texture.blit(self.target_size, 0, width=self.target_size, height=self.target_size)
            
        # Update draw FPS counter
        self.draw_fps_counter += 1
        current_time = time.time()
        if current_time - self.draw_fps_start_time >= 5.0:
            draw_fps = self.draw_fps_counter / (current_time - self.draw_fps_start_time)
            print(f"Draw FPS: {draw_fps:.1f}")
            
            # Get frame statistics from distributor
            stats = self.get_frame_stats()
            print(f"Frame buffer: {stats['buffer_size']} frames, current display: {stats['current_display_frame']}, latest received: {stats['latest_received_frame']}, total processed: {stats['total_frames_processed']}")
            
            self.draw_fps_counter = 0
            self.draw_fps_start_time = current_time
    
    def on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            print("\nESC pressed")
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
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Webcam application with distributed video processing')
    parser.add_argument('--distribute-port', type=int, default=5555, 
                       help='Port for distributing frames to workers (default: 5555)')
    parser.add_argument('--collect-port', type=int, default=5556,
                       help='Port for collecting processed frames from workers (default: 5556)')
    parser.add_argument('--frame-delay', type=int, default=5,
                       help='Frame delay for processing (default: 5)')
    parser.add_argument('--target-size', type=int, default=480,
                       help='Target size for frame processing (default: 480)')
    
    args = parser.parse_args()
    
    app = WebcamApp(args.distribute_port, args.collect_port, args.frame_delay, args.target_size)
    app.run()

if __name__ == "__main__":
    main() 
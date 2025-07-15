import pyglet
from pyglet.gl import *
import threading
import time
import signal
import argparse
import subprocess
import numpy as np
import os
import pygame
from distributor import Distributor
from turbojpeg import TurboJPEG

# Constants
CAPTURE_WIDTH = 1920
CAPTURE_HEIGHT = 1080
TARGET_SIZE = 1024
CAMERA_FPS = 20
PROMPT_CYCLE_TIME = 30

class WebcamApp(Distributor):
    def __init__(self, distribute_port=5555, collect_port=5556, frame_delay=8):
        # Initialize parent Distributor class with configurable frame delay
        super().__init__(distribute_port, collect_port, frame_delay)
        
        # Store target size for frame processing
        self.target_size = TARGET_SIZE
        
        self.jpeg = TurboJPEG()
        
        # Pyglet window setup
        config = pyglet.gl.Config(
            double_buffer=True,
            sample_buffers=1,
            samples=4,
            alpha_size=8,
            depth_size=24
        )
        self.window = pyglet.window.Window(
            fullscreen=True,
            config=config,
            vsync=True,
            caption="Transformirror")
        
        self.show_unprocessed_frame = False
        
        self.prompts = self.load_prompts()
        self.current_prompt_idx = 0
        self.last_prompt_change = None
        
        # Initialize pygame mixer for audio
        for attempt in range(3):
            try:
                pygame.mixer.init()
                break
            except pygame.error:
                print("Failed to initialize pygame mixer. Retrying...", flush=True)
                time.sleep(1)
        print("Successfully initialized pygame mixer", flush=True)
        
        # Camera and frame data
        self.frame_data = None
        self.texture = None
        self.processed_texture = None
        self.ffmpeg_pipe = None
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
        
    def load_prompts(self):
        try:
            with open('prompts.txt', 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            return ["A beautiful portrait"]
        
    def get_current_prompt(self):
        current_time = time.time()
        if self.last_prompt_change is None or current_time - self.last_prompt_change >= PROMPT_CYCLE_TIME:
            n = len(self.prompts)
            self.current_prompt_idx = (self.current_prompt_idx + 1) % n
            self.last_prompt_change = current_time
            
            # Play corresponding audio file when prompt changes
            try:
                audio_idx = self.current_prompt_idx % (n // 2)
                audio_file = f"audio/{audio_idx:02d}.wav"
                if os.path.exists(audio_file):
                    pygame.mixer.music.stop()
                    pygame.mixer.music.load(audio_file)
                    pygame.mixer.music.play()
                print(f"Playing audio: {audio_file} ({self.current_prompt_idx} of {n})", flush=True)
            except Exception as e:
                print(f"Error playing audio: {str(e)}", flush=True)
            
        return self.prompts[self.current_prompt_idx]
    
    def _signal_handler(self, signum, frame):
        print(f"\nReceived signal {signum}")
        self.cleanup()
        pyglet.app.exit()
    
    def setup_ffmpeg_pipe(self):
        crop_x = (CAPTURE_WIDTH - self.target_size) // 2
        crop_y = (CAPTURE_HEIGHT - self.target_size) // 2
        
        ffmpeg_cmd = (
            f"ffmpeg -hide_banner -loglevel error "
            f"-f v4l2 -input_format mjpeg -framerate {CAMERA_FPS} "
            f"-video_size {CAPTURE_WIDTH}x{CAPTURE_HEIGHT} -i /dev/video0 "
            f"-vf crop={self.target_size}:{self.target_size}:{crop_x}:{crop_y} "
            "-f rawvideo -pix_fmt rgb24 -"
        )
        
        try:
            self.ffmpeg_pipe = subprocess.Popen(
                ffmpeg_cmd.split(), 
                stdout=subprocess.PIPE, 
                stderr=subprocess.DEVNULL
            )
            print(f"FFmpeg pipe started: {ffmpeg_cmd}")
            return True
        except FileNotFoundError:
            print("Error: ffmpeg not found. Please install ffmpeg.")
            self.running = False
            return False
        except Exception as e:
            print(f"Error starting FFmpeg pipe: {e}")
            self.running = False
            return False
    
    def cleanup_ffmpeg_pipe(self):
        if self.ffmpeg_pipe:
            print("Cleaning up ffmpeg process...")
            try:
                self.ffmpeg_pipe.terminate()
                try:
                    self.ffmpeg_pipe.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    print("FFmpeg process didn't terminate gracefully, forcing kill...")
                    self.ffmpeg_pipe.kill()
                    self.ffmpeg_pipe.wait()
                finally:
                    self.ffmpeg_pipe.stdout.close()
                    self.ffmpeg_pipe = None
                    print("FFmpeg process cleaned up successfully")
            except Exception as e:
                print(f"Error during FFmpeg cleanup: {e}")
    
    def read_frames(self):
        print("Starting FFmpeg video capture...")
        if not self.setup_ffmpeg_pipe():
            return
        
        try:
            while self.running:
                current_time = time.time()
                
                frame_data = self.ffmpeg_pipe.stdout.read(self.target_size * self.target_size * 3)
                if not frame_data:
                    time.sleep(0.01)
                    continue

                try:
                    frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(self.target_size, self.target_size, 3)
                except ValueError as e:
                    print(f"Error reshaping frame data: {e}")
                    continue
                
                # Update capture FPS counter
                self.capture_fps_counter += 1
                if current_time - self.capture_fps_start_time >= 5.0:
                    capture_fps = self.capture_fps_counter / (current_time - self.capture_fps_start_time)
                    print(f"Capture FPS: {capture_fps:.1f}")
                    self.capture_fps_counter = 0
                    self.capture_fps_start_time = current_time
                
                # Store processed frame data for display
                self.frame_data = frame
                self.new_frame_available = True
                
                # Send frame to distributor for distribution to workers
                frame_bytes = self.jpeg.encode(frame)
                prompt = self.get_current_prompt()
                self.add_frame_for_distribution(frame_bytes, current_time, prompt)
        
        except Exception as e:
            print(f"Error in capture loop: {e}")
        finally:
            self.cleanup_ffmpeg_pipe()
            print("Camera released.")
    
    def on_draw(self):
        self.window.clear()
        
        window_width = self.window.width
        window_height = self.window.height
        
        # Update live feed texture if we have new frame data
        if self.show_unprocessed_frame:
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
                self.texture.blit(0, 0, width=window_width, height=window_height)
        else:
            frame_updated = self.update_display_frame()
            if frame_updated:
                frame_data_bytes = self.get_frame_to_display()
                if frame_data_bytes is not None:
                    frame_data_bytes = self.jpeg.decode(frame_data_bytes).tobytes()
                    image_data = pyglet.image.ImageData(
                        self.target_size, self.target_size, 'RGB', 
                        frame_data_bytes
                    )
                    self.processed_texture = image_data.get_texture().get_transform(flip_y=True, flip_x=True)
                    self.processed_texture.anchor_x = 0
                    self.processed_texture.anchor_y = 0
                
            if self.processed_texture:
                self.processed_texture.blit(0, 0, width=window_width, height=window_height)
            
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
        if symbol == pyglet.window.key.U:
            self.show_unprocessed_frame = not self.show_unprocessed_frame
            print(f"Showing unprocessed frame: {self.show_unprocessed_frame}")
        
        if symbol == pyglet.window.key.ESCAPE:
            print("\nESC pressed")
            self.cleanup()
            pyglet.app.exit()
    
    def cleanup(self):
        """Clean up camera and call parent cleanup"""
        self.running = False
        self.cleanup_ffmpeg_pipe()
        
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
    args = parser.parse_args()
    
    app = WebcamApp(args.distribute_port, args.collect_port, args.frame_delay)
    app.run()

if __name__ == "__main__":
    main() 
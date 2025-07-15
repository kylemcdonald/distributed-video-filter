import argparse
from worker import Worker
import cv2
import numpy as np
import time
import signal
from diffusion_processor import DiffusionProcessor
from turbojpeg import TurboJPEG

class DiffusionWorker(Worker):
    def __init__(self, host="localhost", distribute_port=5555, collect_port=5556):
        super().__init__(host, distribute_port, collect_port)
        self.processor = DiffusionProcessor(warmup=f"{self.batch_size}x1024x1024x3")
        
        self.jpeg = TurboJPEG()
        
        # Set up signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        print("Diffusion worker started")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        if not self.shutdown_requested:
            self.shutdown_requested = True
            print(f"\nReceived signal {signum}, shutting down...")
            self.running = False
    
    def __call__(self, frame_bytes_batch, prompt):
        """Run image to image diffusion on the input frame"""
        # Convert bytes to numpy array
        input_frame_batch = [self.jpeg.decode(e) for e in frame_bytes_batch]
        input_frame_batch = [np.float32(e) / 255.0 for e in input_frame_batch]
        processed_frame_batch = self.processor(input_frame_batch, prompt)
        processed_frame_batch = [np.uint8(e * 255) for e in processed_frame_batch]
        return [self.jpeg.encode(e) for e in processed_frame_batch]
    
def main():
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Inverter worker for video processing')
    parser.add_argument('--distribute-port', type=int, default=5555, 
                       help='Port to request frames from webcam app (default: 5555)')
    parser.add_argument('--collect-port', type=int, default=5556,
                       help='Port to send inverted frames to webcam app (default: 5556)')
    
    args = parser.parse_args()
    
    worker = DiffusionWorker("transformirror1.local", args.distribute_port, args.collect_port)
    worker.start()

if __name__ == "__main__":
    main() 
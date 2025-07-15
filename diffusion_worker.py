import argparse
from worker import Worker
import cv2
import numpy as np
import time
import signal
from diffusion_processor import DiffusionProcessor

class DiffusionWorker(Worker):
    def __init__(self, host="localhost", distribute_port=5555, collect_port=5556):
        super().__init__(host, distribute_port, collect_port)
        self.processor = DiffusionProcessor()
        
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
    
    def __call__(self, frame_bytes):
        """Run image to image diffusion on the input frame"""
        # Convert bytes to numpy array
        input_frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(1024, 1024, 3)
        input_frame = np.float32(input_frame) / 255.0
        processed_frame = self.processor([input_frame], "a psychedelic landscape")
        processed_frame = np.uint8(processed_frame[0] * 255)
        return processed_frame 
    
def main():
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Inverter worker for video processing')
    parser.add_argument('--distribute-port', type=int, default=5555, 
                       help='Port to request frames from webcam app (default: 5555)')
    parser.add_argument('--collect-port', type=int, default=5556,
                       help='Port to send inverted frames to webcam app (default: 5556)')
    
    args = parser.parse_args()
    
    worker = DiffusionWorker("100.83.2.63", args.distribute_port, args.collect_port)
    worker.start()

if __name__ == "__main__":
    main() 
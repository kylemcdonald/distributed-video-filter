import argparse
from worker import Worker
import cv2
import numpy as np
import time
import signal

class InverterWorker(Worker):
    def __init__(self, host="localhost", distribute_port=5555, collect_port=5556, delay=0.0):
        super().__init__(host, distribute_port, collect_port)
        self.delay = delay
        
        # Set up signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        print("Inverter worker started")
        print(f"Processing delay: {self.delay} seconds")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        if not self.shutdown_requested:
            self.shutdown_requested = True
            print(f"\nReceived signal {signum}, shutting down...")
            self.running = False
    
    def __call__(self, frame_bytes):
        """Invert the colors of the input frame"""
        # Convert bytes to numpy array
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(480, 480, 3)
        
        # Apply artificial delay if specified
        if self.delay > 0:
            time.sleep(self.delay)
        
        # Invert the image using bitwise NOT
        inverted = cv2.bitwise_not(frame)
        return inverted 
    
def main():
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Inverter worker for video processing')
    parser.add_argument('--distribute-port', type=int, default=5555, 
                       help='Port to request frames from webcam app (default: 5555)')
    parser.add_argument('--collect-port', type=int, default=5556,
                       help='Port to send inverted frames to webcam app (default: 5556)')
    parser.add_argument('--delay', type=float, default=0.0,
                       help='Artificial processing delay in seconds (default: 0.0)')
    
    args = parser.parse_args()
    
    worker = InverterWorker("localhost", args.distribute_port, args.collect_port, args.delay)
    worker.start()

if __name__ == "__main__":
    main() 
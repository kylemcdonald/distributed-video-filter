import cv2
import zmq
import numpy as np
import time
import sys
import json
import os
import argparse
import signal
import tempfile
import shutil

class InverterServer:
    def __init__(self, host="localhost", distribute_port=5555, collect_port=5556, delay=0.0):
        self.host = host
        self.distribute_port = distribute_port
        self.collect_port = collect_port
        self.delay = delay
        self.running = False
        self.shutdown_requested = False
        
        # Get process ID
        self.process_id = os.getpid()
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # DEALER socket to request frames from webcam app
        self.dealer_socket = self.context.socket(zmq.DEALER)
        self.dealer_socket.connect(f"tcp://{self.host}:{distribute_port}")
        
        # PUSH socket to send inverted frames back to webcam app
        self.collect_socket = self.context.socket(zmq.PUSH)
        self.collect_socket.connect(f"tcp://{self.host}:{collect_port}")
        
        # Set up signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        print(f"Inverter server started on ports {distribute_port} (request) and {collect_port} (send)")
        print(f"Process ID: {self.process_id}")
        print(f"Processing delay: {self.delay} seconds")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        if not self.shutdown_requested:
            self.shutdown_requested = True
            print(f"\nReceived signal {signum}, shutting down...")
            self.running = False
    
    def start(self):
        """Start the inverter server"""
        self.running = True
        print("Inverter server is running...")
        
        while self.running:
            try:
                # Send READY message to request a frame
                try:
                    self.dealer_socket.send_string("READY", zmq.NOBLOCK)
                except zmq.Again:
                    # Socket buffer full, wait a bit
                    time.sleep(0.001)
                    continue
                
                # Poll for response with timeout
                if self.dealer_socket.poll(10):  # 10ms timeout
                    start_time = time.time()
                    
                    # Receive frame index and frame data from webcam app
                    frame_index = self.dealer_socket.recv_string(zmq.NOBLOCK)
                    frame_data = self.dealer_socket.recv(zmq.NOBLOCK)
                    
                    # Print frame index
                    print(f"Processing frame {frame_index}")
                    
                    # Convert bytes to numpy array
                    frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(480, 480, 3)
                    
                    # Apply artificial delay if specified
                    if self.delay > 0:
                        time.sleep(self.delay)
                    
                    # Invert the frame
                    inverted_frame = self._invert_frame(frame)
                    
                    end_time = time.time()
                    
                    # Send inverted frame, frame index, and process ID back to webcam app
                    try:
                        self.collect_socket.send_string(frame_index, zmq.SNDMORE | zmq.NOBLOCK)
                        self.collect_socket.send_string(str(self.process_id), zmq.SNDMORE | zmq.NOBLOCK)
                        self.collect_socket.send_string(str(start_time), zmq.SNDMORE | zmq.NOBLOCK)
                        self.collect_socket.send_string(str(end_time), zmq.SNDMORE | zmq.NOBLOCK)
                        self.collect_socket.send(inverted_frame.tobytes(), zmq.NOBLOCK)
                    except zmq.Again:
                        print(f"Failed to send inverted frame {frame_index} - socket buffer full")
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error in inverter server: {e}")
                continue
    
    def stop(self):
        """Stop the inverter server"""
        if not self.running:
            return
            
        self.running = False
        
        try:
            # Close sockets gracefully
            if hasattr(self, 'dealer_socket'):
                self.dealer_socket.close()
            if hasattr(self, 'collect_socket'):
                self.collect_socket.close()
            if hasattr(self, 'context'):
                self.context.term()
        except Exception as e:
            print(f"Error during shutdown: {e}")
        
        print("Inverter server stopped")
    
    def _invert_frame(self, frame):
        """Invert the colors of the input frame"""
        # Invert the image using bitwise NOT
        inverted = cv2.bitwise_not(frame)
        return inverted

def main():
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Inverter server for video processing')
    parser.add_argument('--distribute-port', type=int, default=5555, 
                       help='Port to request frames from webcam app (default: 5555)')
    parser.add_argument('--collect-port', type=int, default=5556,
                       help='Port to send inverted frames to webcam app (default: 5556)')
    parser.add_argument('--delay', type=float, default=0.0,
                       help='Artificial processing delay in seconds (default: 0.0)')
    
    args = parser.parse_args()
    
    server = InverterServer("localhost", args.distribute_port, args.collect_port, args.delay)
    server.start()

if __name__ == "__main__":
    main() 
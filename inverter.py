import cv2
import zmq
import numpy as np
import time
import sys
import json
import os
import argparse

class InverterServer:
    def __init__(self, host="localhost", push_port=5555, pull_port=5556, delay=0.0):
        self.host = host
        self.push_port = push_port
        self.pull_port = pull_port
        self.delay = delay
        self.running = False
        
        # Get process ID
        self.process_id = os.getpid()
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # PUSH socket to receive frames from webcam app
        self.push_socket = self.context.socket(zmq.PULL)
        self.push_socket.connect(f"tcp://{self.host}:{push_port}")
        
        # PULL socket to send inverted frames back to webcam app
        self.pull_socket = self.context.socket(zmq.PUSH)
        self.pull_socket.connect(f"tcp://{self.host}:{pull_port}")
        
        print(f"Inverter server started on ports {push_port} (receive) and {pull_port} (send)")
        print(f"Process ID: {self.process_id}")
        print(f"Processing delay: {self.delay} seconds")
    
    def start(self):
        """Start the inverter server"""
        self.running = True
        print("Inverter server is running...")
        
        while self.running:
            try:
                # Receive frame and frame index from webcam app
                frame_index = self.push_socket.recv_string(zmq.NOBLOCK)
                frame_data = self.push_socket.recv(zmq.NOBLOCK)
                
                # Print frame index
                print(f"Processing frame {frame_index}")
                
                # Convert bytes to numpy array
                frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(480, 480, 3)
                
                # Apply artificial delay if specified
                if self.delay > 0:
                    time.sleep(self.delay)
                
                # Invert the frame
                inverted_frame = self._invert_frame(frame)
                
                # Send inverted frame, frame index, and process ID back to webcam app
                self.pull_socket.send_string(frame_index, zmq.SNDMORE)
                self.pull_socket.send_string(str(self.process_id), zmq.SNDMORE)
                self.pull_socket.send(inverted_frame.tobytes(), zmq.NOBLOCK)
                
            except zmq.Again:
                # No message available, continue
                time.sleep(0.001)
                continue
            except KeyboardInterrupt:
                print("Shutting down inverter server...")
                self.stop()
                break
            except Exception as e:
                print(f"Error in inverter server: {e}")
                continue
    
    def stop(self):
        """Stop the inverter server"""
        self.running = False
        self.push_socket.close()
        self.pull_socket.close()
        self.context.term()
        print("Inverter server stopped")
    
    def _invert_frame(self, frame):
        """Invert the colors of the input frame"""
        # Invert the image using bitwise NOT
        inverted = cv2.bitwise_not(frame)
        return inverted

def main():
    # Parse command line arguments using argparse
    parser = argparse.ArgumentParser(description='Inverter server for video processing')
    parser.add_argument('--push-port', type=int, default=5555, 
                       help='Port to receive frames from webcam app (default: 5555)')
    parser.add_argument('--pull-port', type=int, default=5556,
                       help='Port to send inverted frames to webcam app (default: 5556)')
    parser.add_argument('--delay', type=float, default=0.0,
                       help='Artificial processing delay in seconds (default: 0.0)')
    
    args = parser.parse_args()
    
    server = InverterServer("localhost", args.push_port, args.pull_port, args.delay)
    server.start()

if __name__ == "__main__":
    main() 
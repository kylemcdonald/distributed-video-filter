import cv2
import zmq
import numpy as np
import pickle
import time
import sys

class InverterServer:
    def __init__(self, push_port=5555, pull_port=5556):
        self.push_port = push_port
        self.pull_port = pull_port
        self.running = False
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # PUSH socket to receive frames from webcam app
        self.push_socket = self.context.socket(zmq.PULL)
        self.push_socket.bind(f"tcp://*:{push_port}")
        
        # PULL socket to send inverted frames back to webcam app
        self.pull_socket = self.context.socket(zmq.PUSH)
        self.pull_socket.bind(f"tcp://*:{pull_port}")
        
        print(f"Inverter server started on ports {push_port} (receive) and {pull_port} (send)")
    
    def start(self):
        """Start the inverter server"""
        self.running = True
        print("Inverter server is running...")
        
        while self.running:
            try:
                # Receive frame from webcam app
                frame_data = self.push_socket.recv(zmq.NOBLOCK)
                frame = pickle.loads(frame_data)
                
                # Invert the frame
                inverted_frame = self._invert_frame(frame)
                
                # Send inverted frame back to webcam app
                inverted_data = pickle.dumps(inverted_frame)
                self.pull_socket.send(inverted_data, zmq.NOBLOCK)
                
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
    # Parse command line arguments for ports
    push_port = 5555
    pull_port = 5556
    
    if len(sys.argv) >= 3:
        push_port = int(sys.argv[1])
        pull_port = int(sys.argv[2])
    
    server = InverterServer(push_port, pull_port)
    server.start()

if __name__ == "__main__":
    main() 
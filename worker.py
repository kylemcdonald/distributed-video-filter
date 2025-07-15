import zmq
import time
import os

class Worker:
    def __init__(self, host="localhost", distribute_port=5555, collect_port=5556, batch_size=2):
        self.host = host
        self.distribute_port = distribute_port
        self.collect_port = collect_port
        self.running = False
        self.shutdown_requested = False
        
        self.batch_size = batch_size
        
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
        
        print(f"Worker started on ports {distribute_port} (request) and {collect_port} (send)")
        print(f"Process ID: {self.process_id}")
    
    def start(self):
        """Start the worker"""
        self.running = True
        print("Worker is running...")
        
        frame_index_batch = []
        frame_bytes_batch = []
        
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
                    # Receive frame index and frame data from webcam app
                    frame_index = self.dealer_socket.recv_string(zmq.NOBLOCK)
                    prompt = self.dealer_socket.recv_string(zmq.NOBLOCK)
                    frame_bytes = self.dealer_socket.recv(zmq.NOBLOCK)
                    frame_index_batch.append(frame_index)
                    frame_bytes_batch.append(frame_bytes)
                    
                    # Print frame index
                    # print(f"Received frame {frame_index} into {len(frame_bytes_batch)}")
                    
                    if len(frame_index_batch) < self.batch_size:
                        continue
                    
                    # Process the frame using the worker's __call__ method
                    start_time = time.time()
                    processed_frame_batch = self(frame_bytes_batch, prompt)
                    end_time = time.time()
                    
                    # Send processed frame, frame index, and process ID back to webcam app
                    try:
                        for i in range(self.batch_size):
                            self.collect_socket.send_string(frame_index_batch[i], zmq.SNDMORE | zmq.NOBLOCK)
                            self.collect_socket.send_string(str(self.process_id), zmq.SNDMORE | zmq.NOBLOCK)
                            self.collect_socket.send_string(str(start_time), zmq.SNDMORE | zmq.NOBLOCK)
                            self.collect_socket.send_string(str(end_time), zmq.SNDMORE | zmq.NOBLOCK)
                            self.collect_socket.send(processed_frame_batch[i], zmq.NOBLOCK)
                    except zmq.Again:
                        print(f"Failed to send processed frame {frame_index} - socket buffer full")
                    finally:
                        frame_index_batch = []
                        frame_bytes_batch = []
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error in worker: {e}")
                continue
    
    def __call__(self, frame):
        """Process the input frame - to be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement __call__ method")

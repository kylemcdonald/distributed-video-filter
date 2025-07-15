import zmq
import threading
import time
import queue
import json
import os

class Distributor:
    def __init__(self, distribute_port=5555, collect_port=5556, frame_delay=5, enable_trace_export=False):
        # Frame processing queue
        self.frame_queue = queue.Queue(maxsize=10)
        
        # Frame index counter
        self.frame_index_counter = 0
        
        # Track the last frame sent to any client to prevent duplicates
        self.last_frame_sent = -1
        
        # Frame reordering system
        self.received_frames = {}  # Dictionary to store received frames by index
        self.current_display_frame = 0  # Current frame being displayed
        self.latest_received_frame = -1  # Latest frame index received from workers
        self.frame_buffer_size = 50  # Maximum number of frames to keep in buffer
        self.frame_delay = frame_delay  # Number of frames to delay display
        
        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()
        
        # ROUTER socket to handle client requests and send frames
        self.distribute_socket = self.context.socket(zmq.ROUTER)
        self.distribute_socket.bind(f"tcp://*:{distribute_port}")
        
        # PULL socket to receive inverted frames from inverter
        self.collect_socket = self.context.socket(zmq.PULL)
        self.collect_socket.bind(f"tcp://*:{collect_port}")
        
        # Frame timing tracking
        self.enable_trace_export = enable_trace_export
        self.frame_timings = []
        self.trace_start_time = time.time()
        
        # Threading
        self.running = False
        
        # Start distribute handling thread
        self.distribute_thread = threading.Thread(target=self.handle_distribute_requests)
        self.distribute_thread.daemon = True
        
        # Start inverter output checking thread
        self.inverter_thread = threading.Thread(target=self.check_inverter_output)
        self.inverter_thread.daemon = True
    
    def start(self):
        """Start all distributor threads"""
        self.running = True
        self.distribute_thread.start()
        self.inverter_thread.start()
    
    def stop(self):
        """Stop all distributor threads"""
        self.running = False
    
    def log_frame_timing(self, frame_index, timestamp, event_type="frame_captured"):
        """Log frame timing event for Perfetto trace"""
        if not self.enable_trace_export:
            return
        self.frame_timings.append({
            'frame_index': frame_index,
            'timestamp': timestamp,
            'event_type': event_type,
            'relative_time': timestamp - self.trace_start_time,
            'event_ph': 'i'  # Instant event
        })
    
    def log_frame_complete_timing(self, frame_index, begin_time, end_time, event_type="frame_processed", pid=None):
        """Log frame timing as complete event with duration"""
        if not self.enable_trace_export:
            return
        self.frame_timings.append({
            'frame_index': frame_index,
            'begin_time': begin_time,
            'end_time': end_time,
            'event_type': event_type,
            'begin_relative_time': begin_time - self.trace_start_time,
            'end_relative_time': end_time - self.trace_start_time,
            'event_ph': 'X',  # Complete event
            'pid': pid
        })
    
    def export_perfetto_trace(self):
        """Export frame timing data to Perfetto trace format"""
        if not self.enable_trace_export:
            print("Trace export is disabled")
            return
        if not self.frame_timings:
            print("No frame timing data to export")
            return
        
        # Create trace file
        trace_file = "webcam_frame_timing.pftrace"
        
        # Write trace data in Perfetto format
        with open(trace_file, 'w') as f:
            f.write('{\n')
            f.write('  "traceEvents": [\n')
            
            for i, timing in enumerate(self.frame_timings):
                if timing['event_ph'] == 'i':  # Instant event
                    event = {
                        "name": f"Frame {timing['frame_index']} - {timing['event_type']}",
                        "cat": "video_frames",
                        "ph": "i",  # Instant event
                        "ts": int(timing['relative_time'] * 1000000),  # Convert to microseconds
                        "pid": os.getpid(),
                        "tid": threading.get_ident(),
                        "args": {
                            "frame_index": timing['frame_index'],
                            "event_type": timing['event_type'],
                            "absolute_timestamp": timing['timestamp']
                        }
                    }
                else:  # Complete event
                    duration = timing['end_relative_time'] - timing['begin_relative_time']
                    event = {
                        "name": f"Frame {timing['frame_index']} - {timing['event_type']}",
                        "ph": "X",  # Complete event
                        "ts": int(timing['begin_relative_time'] * 1000000),  # Convert to microseconds
                        "dur": int(duration * 1000000),  # Duration in microseconds
                        "pid": timing.get('pid', os.getpid()),
                        "tid": threading.get_ident(),
                        "args": {
                            "frame_index": timing['frame_index'],
                            "event_type": timing['event_type'],
                            "begin_timestamp": timing['begin_time'],
                            "end_timestamp": timing['end_time'],
                            "duration_ms": duration * 1000
                        }
                    }
                
                f.write('    ' + json.dumps(event))
                if i < len(self.frame_timings) - 1:
                    f.write(',')
                f.write('\n')
            
            f.write('  ]\n')
            f.write('}\n')
        
        print(f"Perfetto trace exported to: {trace_file}")
        print(f"Total frames logged: {len(self.frame_timings)}")
        
        # Print timing statistics
        if self.frame_timings:
            # Separate instant and complete events for statistics
            instant_events = [t for t in self.frame_timings if t['event_ph'] == 'i']
            complete_events = [t for t in self.frame_timings if t['event_ph'] == 'X']
            
            if instant_events:
                timestamps = [t['timestamp'] for t in instant_events]
                intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    print(f"Average frame capture interval: {avg_interval*1000:.2f}ms")
                    print(f"Frame capture rate: {1/avg_interval:.1f} FPS")
            
            if complete_events:
                durations = [(t['end_time'] - t['begin_time']) for t in complete_events]
                if durations:
                    avg_duration = sum(durations) / len(durations)
                    print(f"Average processing duration: {avg_duration*1000:.2f}ms")
                    print(f"Processing rate: {1/avg_duration:.1f} FPS")
                    print(f"Total frames processed: {len(complete_events)}")
    
    def add_frame_for_distribution(self, frame, timestamp=None, prompt=None):
        """Add a frame to the distribution queue with automatic frame indexing"""
        if timestamp is None:
            timestamp = time.time()
        
        # Increment frame index counter
        frame_index = self.frame_index_counter
        self.frame_index_counter += 1
        
        try:
            frame_data = {
                'frame': frame,
                'frame_index': frame_index,
                'timestamp': timestamp,
                'prompt': prompt
            }
            self.frame_queue.put_nowait(frame_data)
            
            # Log frame timing for Perfetto trace
            self.log_frame_timing(frame_index, timestamp, "frame_captured")
            
        except queue.Full:
            # Try to clear old frames and retry once
            try:
                # Remove oldest frame to make room
                _ = self.frame_queue.get_nowait()
                # Retry putting the new frame
                self.frame_queue.put_nowait(frame_data)
                print(f"Replaced old frame with new frame {frame_index}")
            except queue.Full:
                # Still full, drop the frame
                print(f"Frame {frame_index} dropped due to queue overflow")
    
    def handle_distribute_requests(self):
        """Handle client requests for frames via ROUTER socket and process frames from queue"""
        while self.running:
            try:
                # Process frames from queue first
                try:
                    frame_data = self.frame_queue.get_nowait()
                    
                    # Store processed frame data with metadata
                    self.current_frame_data = {
                        'frame': frame_data['frame'],
                        'frame_index': frame_data['frame_index'],
                        'prompt': frame_data['prompt']
                    }
                    
                except queue.Empty:
                    # No frames to process, continue to handle client requests
                    pass
                
                # Poll for messages from clients with shorter timeout
                if self.distribute_socket.poll(10):  # 10ms timeout for more responsiveness
                    # Receive client identity and message
                    client_id = self.distribute_socket.recv(zmq.NOBLOCK)
                    message = self.distribute_socket.recv_string(zmq.NOBLOCK)
                    
                    if message == "READY":
                        # Client is ready for a frame
                        if hasattr(self, 'current_frame_data') and self.current_frame_data is not None and self.current_frame_data.get('frame_index') is not None:
                            # Check if this is a new frame that hasn't been sent yet
                            if self.current_frame_data['frame_index'] > self.last_frame_sent:
                                try:
                                    # Send frame index and frame data to client
                                    self.distribute_socket.send(client_id, zmq.SNDMORE)
                                    self.distribute_socket.send_string(str(self.current_frame_data['frame_index']), zmq.SNDMORE)
                                    self.distribute_socket.send_string(self.current_frame_data['prompt'], zmq.SNDMORE)
                                    self.distribute_socket.send(self.current_frame_data['frame'], zmq.NOBLOCK)
                                    
                                    # Update tracking
                                    self.last_frame_sent = self.current_frame_data['frame_index']
                                    
                                except zmq.Again:
                                    print(f"Failed to send frame {self.current_frame_data.get('frame_index', 'unknown')} - socket buffer full")
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error handling distribute request: {e}")
                continue
    
    def check_inverter_output(self):
        """Check for inverted frames from inverter output queue and store in buffer"""
        while self.running:
            try:
                # Poll for messages with shorter timeout
                if self.collect_socket.poll(10):  # 10ms timeout for more responsiveness
                    # Receive inverted frame from inverter
                    frame_index = self.collect_socket.recv_string(zmq.NOBLOCK)
                    process_id = self.collect_socket.recv_string(zmq.NOBLOCK)
                    start_time = self.collect_socket.recv_string(zmq.NOBLOCK)
                    end_time = self.collect_socket.recv_string(zmq.NOBLOCK)
                    inverted_data = self.collect_socket.recv(zmq.NOBLOCK)
                    
                    if int(frame_index) > self.frame_index_counter:
                        print(f"Dropping frame index {frame_index} greater than frame index counter {self.frame_index_counter}")
                        continue
                    
                    # Log frame timing as complete event with duration
                    self.log_frame_complete_timing(int(frame_index), float(start_time), float(end_time), "frame_inverted_received", int(process_id))
                    
                    # Store frame in buffer with metadata (don't reshape here - let the app handle it)
                    frame_index_int = int(frame_index)
                    self.received_frames[frame_index_int] = {
                        'frame_data': inverted_data,  # Store raw bytes
                        'process_id': process_id,
                        'start_time': float(start_time),
                        'end_time': float(end_time)
                    }
                    
                    # Update latest received frame
                    self.latest_received_frame = max(self.latest_received_frame, frame_index_int)
                    
                    # Clean up old frames from buffer
                    self.cleanup_old_frames()
                
            except zmq.Again:
                # No message available, continue
                continue
            except Exception as e:
                print(f"Error receiving inverted frame: {e}")
                continue
    
    def cleanup_old_frames(self):
        """Remove frames from buffer that are older than current display frame"""
        frames_to_remove = []
        for frame_index in self.received_frames:
            if frame_index < self.current_display_frame:
                frames_to_remove.append(frame_index)
        
        for frame_index in frames_to_remove:
            del self.received_frames[frame_index]
        
        # Also limit buffer size to prevent memory issues
        if len(self.received_frames) > self.frame_buffer_size:
            # Remove oldest frames when buffer is too large
            sorted_frames = sorted(self.received_frames.keys())
            frames_to_remove = sorted_frames[:len(sorted_frames) - self.frame_buffer_size]
            for frame_index in frames_to_remove:
                del self.received_frames[frame_index]
    
    def get_frame_to_display(self):
        """Get the frame to display based on current display frame index"""
        target_frame = self.current_display_frame
        if target_frame in self.received_frames:
            return self.received_frames[target_frame]['frame_data']  # Return raw bytes
        else:
            # Frame is missing, check if we have any frames to display
            if self.received_frames:
                # Find the closest available frame
                available_frames = sorted(self.received_frames.keys())
                if available_frames:
                    closest_frame = min(available_frames, key=lambda x: abs(x - target_frame))
                    return self.received_frames[closest_frame]['frame_data']  # Return raw bytes
        return None
    
    def update_display_frame(self):
        """Update the current display frame based on latest received frame"""
        if self.latest_received_frame >= self.frame_delay:
            # Calculate target frame (frame_delay frames behind latest)
            target_frame = self.latest_received_frame - self.frame_delay
            
            # Only advance if we have the target frame or if we're falling behind
            if target_frame in self.received_frames or target_frame > self.current_display_frame:
                self.current_display_frame = target_frame
                return True
            else:
                # Frame is missing from buffer, count as dropped
                # Still advance to prevent getting stuck
                self.current_display_frame = target_frame
                return True
        elif self.latest_received_frame > 0:
            # If we have some frames but not enough for the delay, advance to latest
            if self.current_display_frame < self.latest_received_frame:
                self.current_display_frame = self.latest_received_frame
                return True
        return False
    
    def get_frame_stats(self):
        """Get current frame statistics for display"""
        return {
            'buffer_size': len(self.received_frames),
            'current_display_frame': self.current_display_frame,
            'latest_received_frame': self.latest_received_frame,
            'frame_delay': self.frame_delay,
            'total_frames_processed': self.frame_index_counter
        }
    
    def cleanup(self):
        """Clean up ZeroMQ connections"""
        self.stop()
        
        # Close ZeroMQ sockets
        self.distribute_socket.close()
        self.collect_socket.close()
        self.context.term()
        print("ZeroMQ connections closed")
        
        # Print frame reordering statistics
        print(f"Frame reordering statistics:")
        print(f"  Latest received frame: {self.latest_received_frame}")
        print(f"  Current display frame: {self.current_display_frame}")
        print(f"  Frames in buffer: {len(self.received_frames)}")
        print(f"  Frame delay: {self.frame_delay} frames")
        
        # Export trace if not already done
        if self.enable_trace_export and self.frame_timings:
            print("Exporting Perfetto trace on cleanup...")
            self.export_perfetto_trace() 
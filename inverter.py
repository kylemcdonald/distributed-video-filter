import argparse
from worker import InverterWorker

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
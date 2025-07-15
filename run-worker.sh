#!/bin/bash

cleanup() {
    kill -- -$$
    exit 1
}

trap cleanup SIGINT

$HOME/anaconda3/envs/transformirror/bin/python diffusion_worker.py &

wait

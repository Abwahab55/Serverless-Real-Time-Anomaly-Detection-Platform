#!/usr/bin/env python3
"""
StreamSense — Local Test Runner
Trains all three profiles locally without AWS/SageMaker dependencies.
"""

import sys
import subprocess

def run_test():
    profiles = ["iot", "finance", "network"]
    
    print("=" * 60)
    print("StreamSense — Local Training Test")
    print("=" * 60)
    
    for profile in profiles:
        print(f"\n[Test] Training {profile} model...")
        try:
            result = subprocess.run(
                [sys.executable, "train_model.py", "--profile", profile],
                capture_output=False,
                timeout=300
            )
            if result.returncode == 0:
                print(f"✓ {profile.upper()} training completed")
            else:
                print(f"✗ {profile.upper()} training failed with code {result.returncode}")
                return False
        except Exception as e:
            print(f"✗ {profile.upper()} training error: {e}")
            return False
    
    print("\n" + "=" * 60)
    print("All profiles trained successfully!")
    print("Check ml/artifacts/ for model files and metadata.")
    print("=" * 60)
    return True

if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)

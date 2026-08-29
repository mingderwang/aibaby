# Development container for AiBaby.
#
# - Runs on CPU by default (torch CPU build) so it works anywhere.
# - For GPU support use a CUDA base image (e.g. nvidia/cuda) or PyTorch's
#   official GPU image and adjust the install line.
# - TensorBoard is served on port 6006.

FROM python:3.11-slim

WORKDIR /app

# Copy dependency manifests first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# TensorBoard + training.
EXPOSE 6006

# Default command: run a short sanity training run.
CMD ["python", "-m", "aibaby.scripts.train", "--config", "aibaby/configs/default.yaml"]

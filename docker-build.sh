# Run a short training run inside the container.
echo ">> Building aibaby image..."
docker build -t aibaby .

echo ">> Running sanity training (25 iterations)..."
docker run --rm -v "$(pwd)/runs:/app/runs" aibaby \
  python -m aibaby.scripts.train --config aibaby/configs/default.yaml --total-iters 25

echo ">> TensorBoard (log your runs/ dir there):"
echo "   docker run --rm -p 6006:6006 -v \"$(pwd)/runs:/app/runs\" aibaby tensorboard --logdir /app/runs --host 0.0.0.0"

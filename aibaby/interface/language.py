"""Forward-looking language-emergence and AGI integration points.

The AI Baby's primary policy is a small Transformer. This package documents and
provides the extension seams needed for future experiments:

  1. **Language emergence / discrete communication** - attach token emission
     and reception to the baby so it can develop an internal 'vocabulary' via
     reinforcement (a communication channel or grounded word-meaning bindings).

  2. **Sequence / memory interface** - the actor-critic currently sees a single
     packed observation. The transformer already supports a *sequence* input
     (causal attention with positional embeddings) so observations, internal
     'utterances', and world-history can be concatenated over time.

  3. **Shared world protocol** - clean observation/action/token schemas that
     other agents (and future multi-agent setups) can implement against.

Nothing here is active in the default training loop; these are deliberately
thin, tested stubs so the project can grow into language/AGI experiments
without rearchitecting.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np

# Reserved token-id namespace. In a language-emergence experiment these could
# be grounded to discrete symbols (see `Tokenizer` below).
ACTION_TOKENS = list(range(5))  # stay, up, down, left, right
WORLD_SYMBOLS = list(range(5, 10))  # e.g. food, hazard, wall, baby, empty

# A baby can, in principle, emit a small bounded vocabulary of internal tokens.
VOCAB_BASE = 16  # starting vocabulary size for emergent communication


class Tokenizer:
    """Deterministic mapping between world events and a discrete token stream.

    This is the minimal seam for 'language emergence': instead of (or in
    addition to) raw observations, the baby consumes and produces sequences of
    discrete tokens. In a full experiment the vocabulary, its size, and the
    mapping would themselves be learned / emerged rather than fixed here.
    """

    def __init__(self, vocab_size: int = VOCAB_BASE):
        self.vocab_size = vocab_size
        # Placeholder grounded symbols (food/hazard/etc.) slotted into the
        # reserved WORLD_SYMBOLS range.
        self._grounding: dict[int, str] = {}

    def ground(self, token: int, meaning: str) -> None:
        """Bind a token to a meaning (in a real experiment this is learned)."""
        if token >= self.vocab_size:
            raise ValueError(f"token {token} out of vocabulary")
        self._grounding[token] = meaning

    def encode_observation(self, obs: np.ndarray) -> List[int]:
        """Convert a flat observation into a short token sequence.

        Heuristic: emit one token per food/hazard/wall cell found plus the
        agent position. Serves as a testable anchor for emergent-communication
        experiments; the learned system would replace this.
        """
        grid = obs[:100]  # 10x10 tile encoding flattened
        tiles = grid.reshape(10, 10)
        tokens: List[int] = []
        for i in range(10):
            for j in range(10):
                v = tiles[i, j]
                if v == 1.0:
                    tokens.append(5)  # food symbol
                elif v == -1.0:
                    tokens.append(6)  # hazard symbol
                elif v == -2.0:
                    tokens.append(7)  # wall symbol
        return tokens[: self.vocab_size]


class SequenceBuilder:
    """Concatenates per-step observations into a variable-length token/vector
    sequence for the transformer's causal (sequence) interface.

    This is the seam that lets the baby hold 'memory' or 'an utterance' over
    time, enabling future language-emergence and long-horizon-AGI experiments.
    """

    def __init__(self, max_seq: int = 64):
        self.max_seq = max_seq
        self._seq: List[np.ndarray] = []

    def push(self, obs: np.ndarray) -> None:
        self._seq.append(obs)
        if len(self._seq) > self.max_seq:
            self._seq.pop(0)

    def sequence(self) -> np.ndarray:
        """Stack stored observations; pads or truncates to max_seq rows."""
        if not self._seq:
            return np.zeros((0,), dtype=np.float32)
        arr = np.stack(self._seq[-self.max_seq :])
        return arr

    def clear(self) -> None:
        self._seq.clear()


class CommunicationChannel:
    """Bidirectional discrete channel between two (or more) babies.

    For language-emergence experiments: each agent can emit text/tokens onto a
    shared channel, and receive others' emissions as part of its observation.
    The default training loop does not use this; it exists so the world can be
    extended to multi-agent grounded communication later.
    """

    def __init__(self):
        self._messages: List[Tuple[int, List[int]]] = []

    def emit(self, speaker_id: int, tokens: List[int]) -> None:
        self._messages.append((speaker_id, list(tokens)))

    def read(self) -> List[Tuple[int, List[int]]]:
        return list(self._messages)

    def reset(self) -> None:
        self._messages.clear()

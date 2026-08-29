"""Small causal Transformer used as a shared actor-critic policy network.

The AI Baby is initialized from scratch (random initialization) - no
pretrained weights, no LLM, no LoRA. It is a deliberately tiny decoder-only
transformer deep enough to learn simple gridworld dynamics at scale.

Interface: `forward(embedded_sequence) -> (logits, value)`.
"""

from __future__ import annotations

from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


class CausalSelfAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        assert d_model % n_heads == 0, "d_model must be divisible by n_heads"
        self.n_heads = n_heads
        self.head_dim = d_model // n_heads
        self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.drop = nn.Dropout(dropout)
        # Causal mask over the *sequence* dimension (persistent buffer).
        self.register_buffer("bias", torch.tril(torch.ones(1, 1, 1024, 1024)))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, C = x.shape
        qkv = self.qkv(x)
        q, k, v = qkv.chunk(3, dim=-1)
        q = q.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        k = k.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        v = v.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)

        att = (q @ k.transpose(-2, -1)) / (self.head_dim ** 0.5)
        att = att.masked_fill(self.bias[:, :, :T, :T] == 0, float("-inf"))
        att = F.softmax(att, dim=-1)
        att = self.drop(att)
        y = (att @ v).transpose(1, 2).contiguous().view(B, T, C)
        return self.proj(y)


class MLP(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.0):
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(d_model, 4 * d_model),
            nn.GELU(),
            nn.Linear(4 * d_model, d_model),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


class TransformerBlock(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = CausalSelfAttention(d_model, n_heads, dropout)
        self.ln2 = nn.LayerNorm(d_model)
        self.mlp = MLP(d_model, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


class BabyTransformer(nn.Module):
    """Randomly-initialized small decoder transformer actor-critic.

    Args:
        obs_dim:  flattened observation size (policy sees the whole grid).
        num_actions: size of the action space.
        d_model:   embedding width.
        n_heads:   number of attention heads.
        n_layers:  number of transformer blocks.
        max_seq:   maximum sequence length (future language/AGI interface).
        drop_emb:  embedding dropout probability.
    """

    def __init__(
        self,
        obs_dim: int,
        num_actions: int = 5,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 3,
        max_seq: int = 512,
        drop_emb: float = 0.0,
    ):
        super().__init__()
        self.obs_dim = obs_dim
        self.num_actions = num_actions
        self.d_model = d_model
        self.max_seq = max_seq

        self.in_proj = nn.Linear(obs_dim, d_model)
        self.pos_emb = nn.Embedding(max_seq, d_model)
        self.drop = nn.Dropout(drop_emb)
        self.blocks = nn.ModuleList(
            [TransformerBlock(d_model, n_heads, drop_emb) for _ in range(n_layers)]
        )
        self.ln_f = nn.LayerNorm(d_model)
        self.policy_head = nn.Linear(d_model, num_actions)
        self.value_head = nn.Linear(d_model, 1)

        # Policy head initialized to near-zero so early exploration is uniform.
        nn.init.zeros_(self.policy_head.weight)
        nn.init.zeros_(self.policy_head.bias)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Forward pass.

        Args:
            x: (B, obs_dim) observation tensors. Treated as a length-1 sequence.

        Returns:
            logits: (B, num_actions)
            value:  (B, 1)
        """
        B = x.shape[0]
        h = self.in_proj(x).unsqueeze(1)  # (B, 1, d_model)
        h = h + self.pos_emb(torch.zeros(B, dtype=torch.long, device=x.device)).unsqueeze(1)
        h = self.drop(h)
        for blk in self.blocks:
            h = blk(h)
        h = self.ln_f(h)[:, -1, :]  # (B, d_model)
        logits = self.policy_head(h)
        value = self.value_head(h)
        return logits, value


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

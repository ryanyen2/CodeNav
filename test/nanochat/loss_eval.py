"""
A number of functions that help with evaluating a base model.
"""
import math
import torch
import torch.distributed as dist

@torch.no_grad()
def evaluate_perplexity(model, batches, steps):
    """
    Computes standard perplexity (exp of mean cross-entropy loss) over a fixed
    number of batches. Unlike evaluate_bpb, this metric is not normalized by
    token byte length, so it is comparable only across models with the same
    vocabulary. It is provided as a complementary metric when a quick relative
    comparison within the same tokenizer is needed.
    """
    total_loss = torch.tensor(0.0, dtype=torch.float32, device=model.get_device())
    total_tokens = torch.tensor(0, dtype=torch.int64, device=model.get_device())
    batch_iter = iter(batches)
    for _ in range(steps):
        x, y = next(batch_iter)
        loss2d = model(x, y, loss_reduction='none')
        loss2d = loss2d.view(-1)
        y = y.view(-1)
        valid = y >= 0
        total_loss += loss2d[valid].sum()
        total_tokens += valid.sum()
    world_size = dist.get_world_size() if dist.is_initialized() else 1
    if world_size > 1:
        dist.all_reduce(total_loss, op=dist.ReduceOp.SUM)
        dist.all_reduce(total_tokens, op=dist.ReduceOp.SUM)
    mean_loss = total_loss.item() / max(total_tokens.item(), 1)
    return math.exp(mean_loss)


def _normalize_loss(total_nats, total_bytes):
    """Helper: convert nats+bytes to bpb."""
    if total_bytes == 0:
        return float('inf')
    return total_nats / (math.log(2) * total_bytes)

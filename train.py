from argparse import ArgumentParser
from urllib.request import urlopen

import lightning as L
import torch
from torch.utils.data import DataLoader

from lightning_gpt import callbacks, data, models


def main(args):
    with urlopen("https://cs.stanford.edu/people/karpathy/char-rnn/shakespeare_input.txt") as f:
        text = f.read()

    text = text[:1000000]
    train_dataset = data.CharDataset(text, args.block_size)

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, num_workers=args.num_workers)

    GPT_class = None
    extra_kwargs = {}

    if args.implementation == "mingpt":
        GPT_class = models.MinGPT
        extra_kwargs.update(
            dict(
                embd_pdrop=0.1,
                resid_pdrop=0.1,
                attn_pdrop=0.1,
            )
        )

    elif args.implementation == "nanogpt":
        GPT_class = models.NanoGPT
        extra_kwargs["dropout"] = 0.0

    else:
        raise ValueError(f"Unsupported implementation {args.implementation}")

    if 'deepspeed' in args.strategy:
        if GPT_class == models.MinGPT:
            GPT_class = models.DeepSpeedMinGPT
        elif GPT_class == models.NanoGPT:
            GPT_class = models.DeepSpeedNanoGPT
        else:
            raise ValueError(f"Implementation {args.implementation} not supported with DeepSpeed")
        if 'offload' in args.strategy:
            extra_kwargs["fused_adam"] = False
            extra_kwargs["offload"] = True
        else:
            extra_kwargs["fused_adam"] = True
            extra_kwargs["offload"] = False
        extra_kwargs['activation_checkpointing'] = args.activation_checkpointing

    elif 'fsdp' in args.strategy:
        if GPT_class == models.MinGPT:
            GPT_class = models.FSDPMinGPT
        elif GPT_class == models.NanoGPT:
            GPT_class = models.FSDPNanoGPT
        else:
            raise ValueError(f"Implementation {args.implementation} not supported with FSDP")

    model = GPT_class(
        vocab_size=train_dataset.vocab_size,
        block_size=train_dataset.block_size,
        model_type=args.model_type,
        n_layer=args.n_layer,
        n_head=args.n_head,
        n_embd=args.n_embd,
        weight_decay=0.1,
        learning_rate=args.learning_rate,
        betas=(0.9, 0.95),
        **extra_kwargs,
    )

    if args.compile:
        if not hasattr(torch, "compile"):
            raise RuntimeError(
                f"The current torch version ({torch.__version__}) does not have support for compile."
                "Please install torch >= 1.14 or disable compile."
            )
        model = torch.compile(model)

    callback_list = [L.pytorch.callbacks.ModelSummary(-1)]

    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
        callback_list.append(callbacks.CUDAMetricsCallback())

    trainer = L.Trainer.from_argparse_args(
        args,
        #strategy=L.pytorch.strategies.DDPStrategy(find_unused_parameters=False),
        max_epochs=10,
        #gradient_clip_val=1.0,
        callbacks=callback_list,
        accelerator="auto",
        devices="auto",
        precision=16,
    )

    trainer.fit(model, train_loader)

    # context = "friends of my soul"  # Prime with something
    # x = train_dataset.to_tokens(context, model.device)
    # y = model.generate(x, max_new_tokens=1000, temperature=1.0, do_sample=True, top_k=10)
    # print(train_dataset.from_tokens(y))


if __name__ == "__main__":
    L.seed_everything(42)

    parser = ArgumentParser()
    parser = L.Trainer.add_argparse_args(parser)

    parser.add_argument("--model_type", default="gpt2", type=str)
    parser.add_argument("--n_layer", type=int)
    parser.add_argument("--n_head", type=int)
    parser.add_argument("--n_embd", type=int)
    parser.add_argument("--learning_rate", default=3e-4, type=float)
    parser.add_argument("--block_size", default=128, type=int)
    parser.add_argument("--batch_size", default=64, type=int)
    parser.add_argument("--num_workers", default=4, type=int)
    parser.add_argument("--compile", default=None, choices=[None, "dynamo"])
    parser.add_argument("--implementation", default="mingpt", choices=["mingpt", "nanogpt"])
    parser.add_argument("--activation_checkpointing", default=False, type=bool)
    args = parser.parse_args()

    main(args)

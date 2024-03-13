import torch
torch.backends.cuda.matmul.allow_tf32 = True
import torch.nn as nn
from utils import init_distributed, init_wandb
import torch.multiprocessing as mp
import trainers
import wandb
from typing import Optional, Set
import resource
import copy

class Client:

    def __init__(self, client_idx, local_train_data, local_eval_data, config,
                 TrainerClass, policy):
        self.client_idx = client_idx
        self.batch_counter = 0
        self.example_counter = 0

        self.data = {"train": local_train_data, "test": local_eval_data}
        self.train_sample_num = len(local_train_data)

        self.config = config

        self.wandb_run_initialized = False
        self.wandb_id = f"Client{self.client_idx}-{wandb.util.generate_id()}"

        self.policy = policy
        self.TrainerClass = TrainerClass

    def train(self, reference_model: Optional[nn.Module] = None):
        if not self.wandb_run_initialized:
            self.wandb_run_initialized = True
            print(f"########## Initializing wandb run for client {self.client_idx}...... ##########")
            self.wandb_run = init_wandb(self.config, self.wandb_id, self.client_idx)

        if 'FSDP' in self.config.trainer:
            world_size = torch.cuda.device_count()
            print('starting', world_size, 'processes for FSDP training')
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
            print(f'setting RLIMIT_NOFILE soft limit to {hard} from {soft}')
            mp.spawn(self.worker_main,
                     nprocs=world_size,
                     args=(world_size, reference_model),
                     join=True)
        else:
            print('starting single-process worker')
            self.worker_main(0, 1, reference_model)

        print(self.batch_counter, self.example_counter)

    def worker_main(self,
                    rank: int,
                    world_size: int,
                    reference_model: Optional[nn.Module] = None):
        """Main function for each worker process (may be only 1 for BasicTrainer/TensorParallelTrainer)."""
        if 'FSDP' in self.config.trainer:
            init_distributed(rank, world_size, port=self.config.fsdp_port)

        if self.config.debug:
            self.wandb_run.init = lambda *args, **kwargs: None
            self.wandb_run.log = lambda *args, **kwargs: None

        print(f'Creating trainer on process {rank} with world size {world_size}')

        trainer = self.TrainerClass(self.batch_counter,
                                    self.example_counter,
                                    self.wandb_run,
                                    self.client_idx,
                                    self.policy,
                                    self.config,
                                    self.config.seed,
                                    self.config.local_run_dir,
                                    dataset=self.data,
                                    reference_model=reference_model,
                                    rank=rank,
                                    world_size=world_size)
        trainer.train()
        trainer.save()

    def get_policy_params(self):
        return copy.deepcopy(self.policy.state_dict())
    
    def get_train_sample_num(self):
        return self.train_sample_num
    
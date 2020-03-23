from __future__ import print_function, division

import sys
import time

import torch.nn as nn

from data_helpers.data_helper import *
from networks.MYMCD import MYMCD
from solvers.Solver import Solver
import torch.nn.functional as F


class MYMCDSolver(Solver):

    def __init__(self, dataset_type, source_domain, target_domain, cuda='cuda:0',
                 pretrained=False,
                 batch_size=36,
                 num_epochs=9999, max_iter_num=9999999, test_interval=500, test_mode=False, num_workers=2,
                 clean_log=False, lr=0.001, gamma=10, loss_weight=3.0, optimizer_type='SGD', num_k=4,data_root_dir='./data'):
        super(MYMCDSolver, self).__init__(
            dataset_type=dataset_type,
            source_domain=source_domain,
            target_domain=target_domain,
            cuda=cuda,
            pretrained=pretrained,
            batch_size=batch_size,
            num_epochs=num_epochs,
            max_iter_num=max_iter_num,
            test_interval=test_interval,
            test_mode=test_mode,
            num_workers=num_workers,
            clean_log=clean_log,
            lr=lr,
            gamma=gamma,
            optimizer_type=optimizer_type,
            data_root_dir=data_root_dir
        )
        self.model_name = 'MYMCD'
        self.iter_num = 0
        self.optimizer_generator = None
        self.optimizer_classifier1 = None
        self.optimizer_classifier2 = None
        self.num_k = num_k
        self.loss_weight = loss_weight
        self.lr = lr

    def set_model(self):
        if self.dataset_type == 'Digits':
            if self.task in ['MtoU', 'UtoM']:
                self.model = MYMCD(n_classes=self.n_classes, base_model='DigitsMU')
            if self.task in ['StoM']:
                self.model = MYMCD(n_classes=self.n_classes, base_model='DigitsStoM')

        if self.dataset_type in ['Office31', 'OfficeHome']:
            self.model = MYMCD(n_classes=self.n_classes, base_model='ResNet50')

        if self.pretrained:
            self.load_model(path=self.models_checkpoints_dir + '/' + self.model_name + '_best_train.pt')

        self.model = self.model.to(self.device)

    def test(self, data_loader):
        self.model.eval()

        corrects1 = 0
        corrects2 = 0
        corrects = 0
        data_num = len(data_loader.dataset)
        processed_num = 0

        for inputs, labels in data_loader:
            sys.stdout.write('\r{}/{}'.format(processed_num, data_num))
            sys.stdout.flush()

            inputs = inputs.to(self.device)
            labels = labels.to(self.device)

            outputs2 = self.model(inputs, outputs1=False, outputs2=True)
            # outputs = nn.Softmax(dim=1)(outputs1) + nn.Softmax(dim=1)(outputs2)
            outputs = outputs2

            _, preds = torch.max(outputs, 1)
            corrects += (preds == labels.data).sum().item()

            processed_num += labels.size()[0]

        acc = corrects / processed_num
        print('\nData size = {} , corrects = {}'.format(processed_num, (corrects1 + corrects2) / 2))

        return 0, acc

    def set_optimizer(self):
        if self.optimizer_type == 'Adam':
            self.optimizer_generator = torch.optim.Adam(
                self.model.get_generator_parameters(),
                lr=self.lr,
                weight_decay=0.0005
            )
            self.optimizer_classifier1 = torch.optim.Adam(
                self.model.get_classifier1_parameters(),
                lr=self.lr,
                weight_decay=0.0005
            )
            self.optimizer_classifier2 = torch.optim.Adam(
                self.model.get_classifier2_parameters(),
                lr=self.lr,
                weight_decay=0.0005
            )

        if self.optimizer_type == 'SGD':
            self.gamma = 10

            self.optimizer_generator = torch.optim.SGD(
                self.model.get_generator_parameters(),
                lr=self.lr,
                momentum=0.9,
                weight_decay=0.0005,
                nesterov=True
            )
            self.optimizer_classifier1 = torch.optim.SGD(
                self.model.get_classifier1_parameters(),
                lr=self.lr,
                momentum=0.9,
                weight_decay=0.0005,
                nesterov=True
            )
            self.optimizer_classifier2 = torch.optim.SGD(
                self.model.get_classifier2_parameters(),
                lr=self.lr,
                momentum=0.9,
                weight_decay=0.0005,
                nesterov=True
            )

    def update_optimizer(self, power=0.75, weight_decay=0.0005):
        """Decay learning rate by a factor of 0.1 every lr_decay_epoch epochs."""
        if self.optimizer_type == 'SGD':
            if self.num_epochs != 999999:
                p = self.epoch / self.num_epochs
            else:
                p = self.iter_num / self.max_iter_num

            lr = self.lr * (1.0 + self.gamma * p) ** (-power)
        else:
            lr = self.lr

        self.cur_lr = lr

        for param_group in self.optimizer_generator.param_groups:
            param_group['lr'] = lr * param_group['lr_mult']
            param_group['weight_decay'] = weight_decay * param_group['decay_mult']

        for param_group in self.optimizer_classifier1.param_groups:
            param_group['lr'] = lr * param_group['lr_mult']
            param_group['weight_decay'] = weight_decay * param_group['decay_mult']

        for param_group in self.optimizer_classifier2.param_groups:
            param_group['lr'] = lr * param_group['lr_mult']
            param_group['weight_decay'] = weight_decay * param_group['decay_mult']

    def reset_optimizer(self):
        self.optimizer_generator.zero_grad()
        self.optimizer_classifier1.zero_grad()
        self.optimizer_classifier2.zero_grad()

    def compute_discrepancy(self, output_t1, output_t2):
        return torch.mean(torch.abs(F.softmax(output_t1, dim=1) - F.softmax(output_t2, dim=1)))

    def train_one_epoch(self):
        since = time.time()
        self.model.train()

        total_loss = 0
        source_corrects = 0

        total_source_num = len(self.data_loader['source']['train'].dataset)
        processed_source_num = 0
        source_iter = iter(self.cycle(self.data_loader['source']['train']))

        for target_inputs, target_labels in self.data_loader['target']['train']:
            sys.stdout.write('\r{}/{}'.format(processed_source_num, total_source_num))
            sys.stdout.flush()

            self.update_optimizer()

            # TODO 1 : Step A
            self.reset_optimizer()

            source_inputs, source_labels = next(source_iter)
            source_inputs = source_inputs.to(self.device)
            source_outputs = self.model(source_inputs,outputs1=True)
            source_labels = source_labels.to(self.device)
            loss_source = nn.CrossEntropyLoss()(source_outputs, source_labels)

            target_inputs = target_inputs.to(self.device)
            target_outputs1, target_outputs2 = self.model(target_inputs, outputs1=True, outputs2=True)
            target_soft_labels = torch.argmax(target_outputs1, 1).detach()
            loss_target = nn.CrossEntropyLoss()(target_outputs2, target_soft_labels)

            loss = loss_source + loss_target

            loss.backward(retain_graph=True)
            self.optimizer_generator.step()
            self.optimizer_classifier1.step()
            self.optimizer_classifier2.step()
            self.reset_optimizer()

            total_loss += loss.item() * source_labels.size()[0]
            _, source_class_preds = torch.max(source_outputs, 1)
            source_corrects += (source_class_preds == source_labels.data).sum().item()
            processed_source_num += source_labels.size(0)

            # TODO 2 : Step B
            source_outputs = self.model(source_inputs, outputs1=True)
            loss_source = nn.CrossEntropyLoss()(source_outputs, source_labels)

            target_outputs1, target_outputs2 = self.model(target_inputs, outputs1=True, outputs2=True)
            target_soft_labels = torch.argmax(target_outputs1, 1).detach()
            loss_target = nn.CrossEntropyLoss()(target_outputs2, target_soft_labels)
            loss_discrepancy = self.compute_discrepancy(target_outputs1, target_outputs2)

            loss = loss_source + loss_target - loss_discrepancy

            loss.backward(retain_graph=True)
            self.optimizer_classifier1.step()
            self.optimizer_classifier2.step()
            self.reset_optimizer()

            # # TODO 3 : Step C

            for i in range(self.num_k):
                target_outputs1, target_outputs2 = self.model(target_inputs, outputs1=True, outputs2=True)
                loss_discrepancy = self.compute_discrepancy(target_outputs1, target_outputs2)

                loss_discrepancy.backward()
                self.optimizer_generator.step()
                self.reset_optimizer()

            # TODO 5 : other parameters
            self.iter_num += 1

        acc = (source_corrects / 2) / processed_source_num
        average_loss = (total_loss / 2) / processed_source_num

        print()
        print('\nData size = {} , corrects = {}'.format(processed_source_num, source_corrects / 2))
        print('Using {:4f}'.format(time.time() - since))
        return average_loss, acc
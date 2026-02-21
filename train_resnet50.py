import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
from torch.utils.data import DataLoader, Subset
from torchvision.models import resnet50, ResNet50_Weights
from tqdm import tqdm
import argparse
import os
import logging
import random
import numpy as np
import platform

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def set_seed(seed=42):
    """Set random seed for reproducibility"""
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    torch.backends.cudnn.deterministic = True

def get_data_loaders(args):
    """Create train and validation data loaders for CIFAR-10"""
    # Data augmentation for training
    train_transform = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])
    
    # Only normalization for validation
    val_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])
    
    # Load datasets
    train_dataset = torchvision.datasets.CIFAR10(
        root='./data', train=True, download=True, transform=train_transform)
    
    val_dataset = torchvision.datasets.CIFAR10(
        root='./data', train=False, download=True, transform=val_transform)
    
    # Use subset for quick testing
    if args.quick_test:
        train_size = min(args.train_subset_size, len(train_dataset))
        val_size = min(args.val_subset_size, len(val_dataset))
        
        # Use random indices for training subset
        train_indices = torch.randperm(len(train_dataset))[:train_size].tolist()
        # Use sequential indices for validation subset
        val_indices = list(range(val_size))
        
        train_dataset = Subset(train_dataset, train_indices)
        val_dataset = Subset(val_dataset, val_indices)
        logger.info(f"Using subset: {train_size} train samples, {val_size} val samples")
    
    # Determine number of workers
    # Fix for macOS shared memory issues - use 0 workers to avoid multiprocessing
    num_workers = args.num_workers
    if platform.system() == "Darwin":  # macOS
        num_workers = 0
        logger.info("Running on macOS - setting num_workers=0 to avoid shared memory issues")
    elif args.quick_test:
        num_workers = 0
        logger.info("Quick test mode - setting num_workers=0 for faster startup")
    
    # Disable pin_memory on macOS or when using 0 workers to avoid shared memory issues
    use_pin_memory = True
    if platform.system() == "Darwin" or num_workers == 0:
        use_pin_memory = False
        logger.info("Disabling pin_memory to avoid shared memory issues")
    
    # Create data loaders
    train_loader = DataLoader(
        train_dataset, batch_size=args.batch_size, shuffle=True, 
        num_workers=num_workers, pin_memory=use_pin_memory)
    
    val_loader = DataLoader(
        val_dataset, batch_size=args.batch_size, shuffle=False, 
        num_workers=num_workers, pin_memory=use_pin_memory)
    
    return train_loader, val_loader

def create_model(num_classes=10, pretrained=False):
    """Create ResNet50 model adapted for CIFAR-10"""
    if pretrained:
        # Use pretrained weights
        weights = ResNet50_Weights.IMAGENET1K_V1
        model = resnet50(weights=weights)
        
        # Modify the first convolutional layer to handle 32x32 inputs
        # Original: Conv2d(3, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3))
        # Modified: Conv2d(3, 64, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1))
        model.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
        
        # Remove the maxpool layer since we have small images
        model.maxpool = nn.Identity()
    else:
        # Initialize with default weights
        model = resnet50()
        # Modify the first convolutional layer for CIFAR-10
        model.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
        model.maxpool = nn.Identity()
    
    # Replace the final classifier layer for CIFAR-10 (10 classes)
    model.fc = nn.Linear(model.fc.in_features, num_classes)
    
    return model

def train_epoch(model, train_loader, criterion, optimizer, device, epoch):
    """Train for one epoch"""
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0
    
    progress_bar = tqdm(train_loader, desc=f"Epoch {epoch+1} [Train]")
    
    for inputs, targets in progress_bar:
        inputs, targets = inputs.to(device), targets.to(device)
        
        optimizer.zero_grad()
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        loss.backward()
        optimizer.step()
        
        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += targets.size(0)
        correct += predicted.eq(targets).sum().item()
        
        # Update progress bar
        progress_bar.set_postfix({
            'Loss': f"{running_loss/len(train_loader):.3f}",
            'Acc': f"{100.*correct/total:.2f}%"
        })
    
    epoch_loss = running_loss / len(train_loader)
    epoch_acc = 100. * correct / total
    
    return epoch_loss, epoch_acc

def validate(model, val_loader, criterion, device):
    """Validate the model"""
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0
    
    with torch.no_grad():
        progress_bar = tqdm(val_loader, desc="Validation")
        
        for inputs, targets in progress_bar:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            loss = criterion(outputs, targets)
            
            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()
            
            # Update progress bar
            progress_bar.set_postfix({
                'Loss': f"{running_loss/len(val_loader):.3f}",
                'Acc': f"{100.*correct/total:.2f}%"
            })
    
    val_loss = running_loss / len(val_loader)
    val_acc = 100. * correct / total
    
    return val_loss, val_acc

def main():
    parser = argparse.ArgumentParser(description='Train ResNet50 on CIFAR-10')
    
    # Training parameters
    parser.add_argument('--epochs', type=int, default=10, help='Number of epochs')
    parser.add_argument('--batch-size', type=int, default=128, help='Batch size')
    parser.add_argument('--lr', type=float, default=0.1, help='Learning rate')
    parser.add_argument('--momentum', type=float, default=0.9, help='Momentum for SGD')
    parser.add_argument('--weight-decay', type=float, default=5e-4, help='Weight decay')
    
    # Data parameters
    parser.add_argument('--num-workers', type=int, default=4, help='Number of data loading workers')
    
    # Model parameters
    parser.add_argument('--pretrained', action='store_true', help='Use pretrained ImageNet weights')
    
    # Quick testing
    parser.add_argument('--quick-test', action='store_true', help='Enable quick testing mode')
    parser.add_argument('--train-subset-size', type=int, default=1000, help='Training subset size for quick testing')
    parser.add_argument('--val-subset-size', type=int, default=100, help='Validation subset size for quick testing')
    
    # Checkpointing
    parser.add_argument('--save-dir', type=str, default='checkpoints', help='Directory to save checkpoints')
    parser.add_argument('--save-freq', type=int, default=1, help='Save checkpoint every N epochs')
    
    # Device
    parser.add_argument('--device', type=str, default='cuda' if torch.cuda.is_available() else 'cpu', 
                        help='Device to use for training')
    
    # Seed
    parser.add_argument('--seed', type=int, default=42, help='Random seed for reproducibility')
    
    args = parser.parse_args()
    
    # Set seed for reproducibility
    set_seed(args.seed)
    
    # Create save directory
    os.makedirs(args.save_dir, exist_ok=True)
    
    # Device
    device = torch.device(args.device)
    logger.info(f"Using device: {device}")
    
    # Data loaders
    train_loader, val_loader = get_data_loaders(args)
    logger.info(f"Train batches: {len(train_loader)}, Val batches: {len(val_loader)}")
    
    # Model
    model = create_model(num_classes=10, pretrained=args.pretrained)
    model = model.to(device)
    
    # Loss and optimizer
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum, 
                          weight_decay=args.weight_decay)
    
    # Learning rate scheduler
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    
    # Training loop
    best_val_acc = 0.0
    
    logger.info("Starting training...")
    for epoch in range(args.epochs):
        # Train
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer, device, epoch)
        
        # Validate
        val_loss, val_acc = validate(model, val_loader, criterion, device)
        
        # Update learning rate
        scheduler.step()
        
        # Log epoch results
        logger.info(f"Epoch {epoch+1}/{args.epochs}:")
        logger.info(f"  Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.2f}%")
        logger.info(f"  Val Loss: {val_loss:.4f}, Val Acc: {val_acc:.2f}%")
        logger.info(f"  LR: {scheduler.get_last_lr()[0]:.6f}")
        
        # Save best model
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_acc': val_acc,
                'val_loss': val_loss,
            }, os.path.join(args.save_dir, 'best_model.pth'))
            logger.info(f"  Saved new best model with val acc: {val_acc:.2f}%")
        
        # Save checkpoint
        if (epoch + 1) % args.save_freq == 0:
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_acc': val_acc,
                'val_loss': val_loss,
            }, os.path.join(args.save_dir, f'checkpoint_epoch_{epoch+1}.pth'))
    
    logger.info(f"Training completed. Best validation accuracy: {best_val_acc:.2f}%")

if __name__ == '__main__':
    main()

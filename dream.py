#%%
import torch
#%%
# CUDA가 사용 가능한지 확인
print(torch.cuda.is_available())

# GPU 장치 이름 확인
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
#%%
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision.transforms as transforms
from torchvision.datasets import MNIST
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
#%%
# GPU 설정
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(f'Using device: {device}')

# 타임스텝 수
T = 1000

# Beta 스케줄링 함수
def beta_schedule(timesteps):
    beta_start = 0.0001
    beta_end = 0.02
    return torch.linspace(beta_start, beta_end, timesteps)

# Beta 값과 alpha 값 정의
betas = beta_schedule(T).to(device)
alphas = 1 - betas
alpha_hat = torch.cumprod(alphas, dim=0)

# 모델 정의 (Denoising U-Net 예시)
class DenoisingUNet(nn.Module):
    def __init__(self):
        super(DenoisingUNet, self).__init__()
        self.model = nn.Sequential(
            nn.Conv2d(1, 64, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1),
            nn.ReLU(),
            nn.Conv2d(64, 1, 3, padding=1)
        )
    
    def forward(self, x, t):
        # t 값을 인코딩하여 x와 함께 사용
        t_emb = torch.ones_like(x) * t.view(-1, 1, 1, 1)
        x = torch.cat([x, t_emb], dim=1)
        return self.model(x)

# 모델 및 손실 함수 정의
model = DenoisingUNet().to(device)
criterion = nn.MSELoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)

# 정방향 확산 과정 (이미지에 노이즈 추가)
def forward_diffusion_process(x_0, t):
    noise = torch.randn_like(x_0).to(device)
    return torch.sqrt(alpha_hat[t]) * x_0 + torch.sqrt(1 - alpha_hat[t]) * noise, noise

# 데이터 로드
transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,))
])
dataset = MNIST(root='./data', train=True, download=True, transform=transform)
dataloader = DataLoader(dataset, batch_size=64, shuffle=True)

# 학습 루프
for epoch in range(10):
    for images, _ in dataloader:
        images = images.to(device)
        optimizer.zero_grad()

        # 타임스텝 랜덤 선택
        t = torch.randint(0, T, (images.size(0),)).long().to(device)
        
        # 이미지에 노이즈 추가 (정방향 과정)
        noised_images, noise = forward_diffusion_process(images, t)

        # 모델이 예측한 노이즈
        predicted_noise = model(noised_images, t)

        # 손실 계산 및 역전파
        loss = criterion(predicted_noise, noise)
        loss.backward()
        optimizer.step()
    
    print(f'Epoch {epoch}, Loss: {loss.item()}')

# 샘플링 과정: 무작위 노이즈에서 이미지 생성
def sample(model, img_size=(1, 28, 28)):
    model.eval()
    with torch.no_grad():
        x = torch.randn((1, *img_size)).to(device)
        for t in range(T - 1, -1, -1):
            z = torch.randn_like(x) if t > 0 else 0
            predicted_noise = model(x, torch.tensor([t]).to(device))
            x = (x - predicted_noise) / torch.sqrt(alphas[t]) + z * torch.sqrt(betas[t])
    return x

# 샘플 생성 및 시각화
sampled_image = sample(model).cpu().squeeze().numpy()

plt.imshow(sampled_image, cmap='gray')
plt.title("Generated Image")
plt.axis('off')
plt.show()

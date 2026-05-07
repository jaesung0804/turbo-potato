#%%
import torch
import torch.nn as nn
import torch.optim as optim

# 데이터를 위한 CUDA 설정 확인
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# 샘플 데이터 생성 (간단한 y = 2x + 1 관계)
# 입력 데이터 (10개의 x 값)
x = torch.tensor([[1.0], [2.0], [3.0], [4.0], [5.0], [6.0], [7.0], [8.0], [9.0], [10.0]], device=device)
# 출력 데이터 (y 값)
y = torch.tensor([[3.0], [5.0], [7.0], [9.0], [11.0], [13.0], [15.0], [17.0], [19.0], [21.0]], device=device)

# 선형 회귀 모델 정의 (입력 크기 1, 출력 크기 1)
class LinearRegressionModel(nn.Module):
    def __init__(self):
        super(LinearRegressionModel, self).__init__()
        self.linear = nn.Linear(1, 1)  # y = Wx + b

    def forward(self, x):
        return self.linear(x)

# 모델 초기화 및 GPU로 이동
model = LinearRegressionModel().to(device)

# 손실 함수와 옵티마이저 정의
criterion = nn.MSELoss()  # Mean Squared Error Loss
optimizer = optim.SGD(model.parameters(), lr=0.01)

# 학습 루프
num_epochs = 1000
for epoch in range(num_epochs):
    # 모델 예측
    y_pred = model(x)
    
    # 손실 계산
    loss = criterion(y_pred, y)
    
    # 역전파 및 가중치 업데이트
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    
    # 100번마다 손실 출력
    if (epoch+1) % 100 == 0:
        print(f'Epoch [{epoch+1}/{num_epochs}], Loss: {loss.item():.4f}')

# 학습 완료 후 모델 파라미터 출력 (기대값: W ~ 2, b ~ 1)
print(f'Weight: {model.linear.weight.item()}, Bias: {model.linear.bias.item()}')

# 모델 테스트
test_input = torch.tensor([[4.0]], device=device)
predicted = model(test_input).item()
print(f'Prediction for input 4.0: {predicted}')

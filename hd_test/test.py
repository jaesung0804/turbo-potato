#%%
import pandas as pd
import numpy as np
import sklearn
#%%
df = pd.read_csv("./data/train.csv")
#%%
df.info()
df.head()

df = df.drop(columns='num_date_time')
#%%
mode_precipitation = df['강수량(mm)'].mode()[0]
df['강수량(mm)'].fillna(mode_precipitation, inplace=True)

df.info()

#%%
df['일조(hr)'] = df['일조(hr)'].interpolate(method='linear')
df['일사(MJ/m2)'] = df['일사(MJ/m2)'].interpolate(method='linear')

df.dropna(inplace= True)

df.info()
#%%
from sklearn.ensemble import RandomForestRegressor

df['일시'] = pd.to_datetime(df['일시'])
df['일시'].head()

#%%
df['연도'] = df['일시'].dt.year
df['월'] = df['일시'].dt.month
df['일'] = df['일시'].dt.day
df['시간'] = df['일시'].dt.hour

#%%
df.info()
df.head()

df.drop(columns = '일시', inplace= True)
df.head()
df.info()
#%%    
y = df['전력소비량(kWh)']
X = df.drop(columns='전력소비량(kWh)')

X.head()

# 범주형 변수 인코딩 (예: One-Hot 인코딩)
X = pd.get_dummies(X, columns=['연도','월', '일', '시간','건물번호'], drop_first=True)

#%%
from sklearn.model_selection import train_test_split
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, 
                                                    random_state=42)

#%%
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder

continuous_cols = ["기온(C)", "강수량(mm)", "풍속(m/s)", "습도(%)",
                   "일조(hr)", "일사(MJ/m2)"]

# 연속형 변수에만 MinMaxScaler 적용
scaler = MinMaxScaler()
X_train[continuous_cols] = scaler.fit_transform(X_train[continuous_cols])
X_test.transform(scaler)
#%%
X_train.head()

#%%
# y_train에 MinMaxScaler 적용
y_scaler = MinMaxScaler()
y_train = y_scaler.fit_transform(y_train.values.reshape(-1, 1))

# y_test에는 transform만 적용
y_test = y_scaler.transform(y_test.values.reshape(-1, 1))

# RandomForestRegressor 모델 학습 및 예측
model = RandomForestRegressor(random_state=42)
model.fit(X_train, y_train.ravel())  # y_train을 1차원으로 변환

# 예측 수행
y_pred = model.predict(X_test)

# 예측 결과를 원래 스케일로 변환
y_pred = y_scaler.inverse_transform(y_pred.reshape(-1, 1))

# 예측 결과 확인
print("예측 결과:", y_pred[:5])

#%%
from sklearn.metrics import mean_squared_error
import numpy as np

# 예측 결과 (y_pred)를 모델로 예측 후 원래 스케일로 되돌려야 합니다
y_pred = model.predict(X_test)
y_pred = y_scaler.inverse_transform(y_pred.reshape(-1, 1))

# MSE 계산
mse = mean_squared_error(y_test, y_pred)
print("Mean Squared Error (MSE):", mse)

# RMSE 계산
rmse = np.sqrt(mse)
print("Root Mean Squared Error (RMSE):", rmse)

#%%
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error

# 예제 데이터 생성
# X와 y가 이미 정의되어 있다고 가정합니다 (예: DataFrame 형태)
# df['X']는 입력 특성, df['y']는 타겟 변수

# 이동 평균 구하기 (X의 각 열에 대해)
kernel_size = 5  # 윈도우 크기
X_ma = X_train.rolling(window=kernel_size, center=True, min_periods=1).mean()  # X의 이동 평균
X_residual = X_train - X_ma  # X의 잔차 계산

# y도 동일한 방식으로 이동 평균과 잔차로 나눕니다
y_train= pd.DataFrame(y_train)
y_ma = y_train.rolling(window=10, center=True, min_periods=1).mean()  # y의 이동 평균
y_residual = y_train - y_ma  # y의 잔차 계산
#%%
# 2. 각각 모델 학습
# 이동 평균을 예측하는 모델
from sklearn.linear_model import ElasticNet

trend_model = ElasticNet()
trend_model.fit(X_ma.iloc[:,:6], y_ma)

# 잔차를 예측하는 모델
residual_model = ElasticNet()
residual_model.fit(X_residual.iloc[:,:6], y_residual)

# 3. 각각 예측 후 최종 예측값 생성
# 이동 평균 구하기 (X의 각 열에 대해)
kernel_size = 10  # 윈도우 크기
X_ma_test = X_test.rolling(window=kernel_size, center=True, min_periods=1).mean()  # X의 이동 평균
X_residual_test = X_test - X_ma_test  # X의 잔차 계산

# y도 동일한 방식으로 이동 평균과 잔차로 나눕니다
y_test= pd.DataFrame(y_test)
y_ma_test = y_test.rolling(window=kernel_size, center=True, min_periods=1).mean()  # y의 이동 평균
y_residual_test = y_test - y_ma_test  # y의 잔차 계산

#최종모델
trend_pred = trend_model.predict(X_ma_test.iloc[:,:6])
residual_pred = residual_model.predict(X_residual_test.iloc[:,:6])

last_input = trend_pred+residual_pred

last_model = ElasticNet()
last_model.fit(last_input, y_train)

prediction = last_model.predict()
y_scaler.transform(y_ma_test) + residual_pred


# 이동 평균과 잔차 예측을 합산
final_prediction= y_scaler.inverse_transform(prediction.reshape(-1, 1))

y_ma_test
# 성능 평가 (예: MSE)
mse = mean_squared_error(y_test, final_prediction)
print("Mean Squared Error:", mse)

rmse= np.sqrt(mse)
print("Root Mean Squared Error (RMSE):", rmse)

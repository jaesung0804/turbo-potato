# GitHub Pages 배포 순서

## 1. 데이터 빌드

AI 모델을 다시 돌리지 않고, 현재 수집된 실거래/교통/교육/초품아 데이터만 웹 JSON에 반영합니다.

```powershell
cd C:\code
python build_real_estate_dashboard_data.py --apt-detail data/apt_basis_detail_info.csv
```

주의: `python run_real_estate_dashboard.py --no-browser`는 웹 서버를 계속 실행하는 명령이라 그 다음 `git add`, `git commit`으로 자동 진행되지 않습니다. 배포 전 데이터 생성은 위의 build 스크립트를 직접 실행하세요.

## 2. 로컬 확인

이미 8000번 포트 서버가 떠 있다면 브라우저에서 새로고침만 하면 됩니다.

새로 서버를 켜야 할 때:

```powershell
python run_real_estate_dashboard.py --skip-build --skip-ai
```

포트가 이미 사용 중이면 기존 서버를 종료합니다.

```powershell
Get-NetTCPConnection -LocalPort 8000
Stop-Process -Id <OwningProcess>
```

## 3. GitHub Pages 배포

GitHub 저장소 Settings > Pages에서 Source를 `GitHub Actions`로 설정한 뒤:

```powershell
git add .gitignore .github README.md GITHUB_PAGES_DEPLOY.md *.py web
git commit -m "open one"
git push origin main
```

배포 주소:

```text
https://jaesung0804.github.io/turbo-potato/
```

## 메모

- `data/`, `요건/`, `__pycache__/`는 Git에 올리지 않습니다.
- `web/data/seoul_real_estate_summary.json`은 GitHub 단일 파일 제한을 넘지 않도록 지역별 매물 표시 개수를 줄여 생성합니다.
- AI 추천 JSON은 내일 모델을 다시 학습하면 새로 갱신하면 됩니다.

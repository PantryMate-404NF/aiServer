pipeline {
  agent { label 'jenkins-agent' }

  environment {
    AWS_REGION      = 'ap-northeast-2'
    ECR_REGISTRY    = '542119828072.dkr.ecr.ap-northeast-2.amazonaws.com'
    ECR_REPO        = 'pantry-mate-dev-ai'
    IMAGE_NAME      = "${ECR_REGISTRY}/${ECR_REPO}"
    GITOPS_REPO     = 'https://github.com/PantryMate-404NF/pantry-mate-gitops.git'
    GITOPS_APP_PATH = 'environments/dev/cloud-test-ai'
  }

  options {
    timeout(time: 60, unit: 'MINUTES')
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '10'))
  }

  stages {

    stage('Checkout') {
      steps {
        checkout scm
        script {
          env.IMAGE_TAG = sh(returnStdout: true, script: 'git rev-parse --short=8 HEAD').trim()
          env.IMAGE_NAME = "${env.ECR_REGISTRY}/${env.ECR_REPO}"
        }
        sh 'git log --oneline -3'
      }
    }

    stage('Build & Push ECR') {
      steps {
        container('dind') {
          sh '''
            apk add --no-cache python3 py3-pip
            pip3 install awscli --break-system-packages --quiet

            aws ecr get-login-password --region $AWS_REGION \
              | docker login --username AWS --password-stdin $ECR_REGISTRY

            # 이미 ECR에 존재하면 빌드/푸시 스킵
            if aws ecr describe-images --region $AWS_REGION \
                --repository-name $ECR_REPO \
                --image-ids imageTag=$IMAGE_TAG > /dev/null 2>&1; then
              echo "Image $IMAGE_TAG already in ECR, skipping build"
            else
              docker build -t $IMAGE_NAME:$IMAGE_TAG .
              docker push $IMAGE_NAME:$IMAGE_TAG || \
                aws ecr describe-images --region $AWS_REGION --repository-name $ECR_REPO --image-ids imageTag=$IMAGE_TAG > /dev/null 2>&1 || \
                { echo "Push failed and image not in ECR"; exit 1; }
            fi

            MANIFEST=$(aws ecr batch-get-image --region $AWS_REGION --repository-name $ECR_REPO --image-ids imageTag=$IMAGE_TAG --query 'images[0].imageManifest' --output text)
            aws ecr put-image --region $AWS_REGION --repository-name $ECR_REPO --image-tag latest --image-manifest "$MANIFEST" || true
          '''
        }
      }
    }

    stage('Update GitOps') {
      when { branch 'main' }
      steps {
        container('dind') {
          withCredentials([usernamePassword(
            credentialsId: 'github-credentials',
            usernameVariable: 'GIT_USER',
            passwordVariable: 'GIT_TOKEN'
          )]) {
            sh '''
              apk add --no-cache git sed

              git clone https://$GIT_USER:$GIT_TOKEN@$(echo $GITOPS_REPO | sed 's|https://||') gitops-repo
              cd gitops-repo
              git config user.email "jenkins@pantry-mate.internal"
              git config user.name "Jenkins CI"

              sed -i "s|image: $ECR_REGISTRY/$ECR_REPO:.*|image: $ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG|g" \
                $GITOPS_APP_PATH/deployment.yaml

              git add $GITOPS_APP_PATH/deployment.yaml
              git diff --cached --quiet || git commit -m "ci: update ai image to $IMAGE_TAG [skip ci]"
              git pull --rebase origin main
              git push origin main
            '''
          }
        }
      }
      post {
        always {
          container('dind') {
            sh 'rm -rf gitops-repo'
          }
        }
      }
    }
  }

  post {
    success  { echo "✅ AI Service 빌드 완료: ${IMAGE_NAME}:${IMAGE_TAG}" }
    failure  { echo "❌ 빌드 실패 — 로그를 확인하세요." }
    cleanup  {
      container('dind') {
        sh "docker rmi ${IMAGE_NAME}:${IMAGE_TAG} || true"
      }
      cleanWs()
    }
  }
}

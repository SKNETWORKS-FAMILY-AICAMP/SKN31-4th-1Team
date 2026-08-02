# server/auth.py
"""
Supabase JWT 로컬 검증 모듈.

기존 방식(supabase_client.auth.get_user(token))은 매 요청마다 Supabase Auth
서버로 왕복하는 '원격 검증'이었다. 이 모듈은 공개키(ES256) 또는 공유 시크릿
(HS256)으로 서명을 '로컬에서 직접' 검증한다. Auth 서버 왕복이 사라지므로
빠르고, 외부 rate limit에 걸리지 않는다.

검증 원리 (JWT = header.payload.signature):
  1) header의 alg/kid를 '서명 확인 없이' 먼저 읽는다.
  2) alg가 ES256이면 -> JWKS 엔드포인트에서 kid에 맞는 공개키를 찾아 서명 검증.
     alg가 HS256이면 -> 프로젝트 JWT 시크릿으로 서명 검증(레거시).
  3) 서명이 맞고, 만료(exp)가 안 지났고, 대상(aud)이 맞아야 통과.

주의: JWT는 '서명'이지 '암호화'가 아니다. payload는 누구나 디코드해서 읽을 수
있으므로 비밀값을 넣으면 안 된다. 신뢰의 근거는 오직 '서명 검증 통과' 여부다.
"""
import os
import logging

import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException, status
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("auth")

SUPABASE_URL = os.getenv("SUPABASE_URL")
# 레거시 HS256 검증용 시크릿. Supabase 대시보드 > Settings > API > JWT Secret.
# (service_role 키가 아니다. 별개다.)
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# Supabase access token의 aud 클레임은 항상 "authenticated".
JWT_AUDIENCE = "authenticated"

# ------------------------------------------------------------------
# JWKS 클라이언트: 모듈 로드 시 '한 번만' 생성한다.
# PyJWKClient가 공개키를 메모리에 캐싱하므로, 매 요청마다 JWKS를 다시
# 내려받지 않는다. 키 로테이션 시 캐시에 없는 kid가 오면 자동 재조회한다.
# ------------------------------------------------------------------
_jwks_client = None
if SUPABASE_URL:
    _jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    # cache_keys=True: 조회한 서명키를 재사용. lifespan 동안 유지.
    _jwks_client = PyJWKClient(_jwks_url, cache_keys=True)
else:
    logger.warning("SUPABASE_URL 미설정 - ES256 토큰을 검증할 수 없습니다.")


def _decode_token(token: str) -> dict:
    """
    토큰을 알고리즘에 맞게 로컬 검증하고 payload(claims)를 반환한다.
    검증 실패 시 jwt.* 예외 또는 RuntimeError(서버 설정 문제)를 던진다.
    """
    # 서명 확인 '전에' 헤더만 먼저 읽어 알고리즘을 판별한다.
    # (헤더는 서명 대상이 아니지만, alg를 봐야 어떤 키로 검증할지 정할 수 있다.
    #  단, alg를 신뢰해 검증을 건너뛰면 안 되고, 아래에서 반드시 서명 검증을 한다.)
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")

    if alg == "ES256":
        if _jwks_client is None:
            raise RuntimeError("JWKS 클라이언트 미초기화 (SUPABASE_URL 확인 필요)")
        # kid에 해당하는 공개키를 JWKS에서 찾아온다 (캐시 우선).
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "sub"]},  # 필수 클레임 강제
        )

    elif alg == "HS256":
        if not SUPABASE_JWT_SECRET:
            raise RuntimeError("SUPABASE_JWT_SECRET 미설정 (HS256 검증 불가)")
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )

    # alg 화이트리스트 밖이면 거부. (예: alg=none 공격 방어)
    raise jwt.InvalidAlgorithmError(f"허용되지 않은 알고리즘: {alg}")


def verify_token(authorization: str = Header(None)) -> dict:
    """
    FastAPI 의존성. Authorization 헤더의 Bearer 토큰을 로컬 검증한다.
    성공 시 claims(dict)를 반환한다. claims["sub"]가 사용자 ID.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 없습니다.",
        )

    token = authorization.split(" ", 1)[1].strip()

    try:
        claims = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 만료되었습니다.")
    except jwt.InvalidAudienceError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰 대상이 올바르지 않습니다.")
    except jwt.InvalidTokenError:
        # 서명 불일치/형식 오류/알고리즘 불일치 등 '검증 실패'는 전부 여기로.
        # 상세 사유는 로그로만 남기고 클라이언트에는 노출하지 않는다.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "유효하지 않은 토큰입니다.")
    except RuntimeError as e:
        # 토큰 문제가 아니라 '서버 설정' 문제. 401이 아니라 500이 맞다.
        logger.error("JWT 검증 서버 설정 오류: %s", e)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "서버 인증 설정 오류",
        )

    return claims

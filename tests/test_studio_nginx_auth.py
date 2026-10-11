from fastapi.testclient import TestClient
from passlib.hash import apr_md5_crypt
from app.main import app
from app.core.config import Settings, get_settings

def test_existing_nginx_operator_hashes_are_reused(tmp_path):
    path=tmp_path/'operators'
    path.write_text('operator:'+apr_md5_crypt.hash('fixture-password')+'\n')
    settings=Settings(app_env='production',studio_htpasswd_file=path,studio_public_origin='https://growth.servgo.ru',_env_file=None)
    app.dependency_overrides[get_settings]=lambda:settings
    try:
        with TestClient(app) as client:
            assert client.get('/products').status_code==401
            assert client.get('/products',auth=('operator','wrong')).status_code==401
            from starlette.requests import Request
            from fastapi.security import HTTPBasicCredentials
            from app.api.content_studio_auth import require_studio_auth
            request=Request({'type':'http','path':'/products','headers':[]})
            credentials=HTTPBasicCredentials(username='operator',password='fixture-password')
            assert require_studio_auth(request,credentials,settings)=='operator'
            assert request.state.studio_product_ids is None
            path.write_text('operator:unsupported\n')
            assert client.get('/products',auth=('operator','fixture-password')).status_code==503
            path.unlink()
            assert client.get('/products',auth=('operator','fixture-password')).status_code==503
    finally:
        app.dependency_overrides.pop(get_settings,None)

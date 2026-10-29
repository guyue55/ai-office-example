"""人工临时连接准备；秘密只在受控服务端内存中使用，不创建配置修订。"""

from types import SimpleNamespace

from app.application.model_runtime import prepare_runtime
from app.modules.agent.model_configuration import ModelDiscover, ModelDraft, ModelProbe
from app.modules.agent.model_store import ModelStore


async def prepare_unsaved(application, access, payload, *, catalog=False, model_context=None):
    command = (ModelDiscover if catalog else ModelProbe).model_validate(payload)
    store = ModelStore(access, application.settings)
    state = await store.state()
    store.expect(state, command.version)
    connection = command.connection if isinstance(command, ModelDiscover) else command.draft
    ciphertext = await store.resolve_secret(state, command, connection)
    draft = ModelDraft.model_validate(
        {**connection.model_dump(), "model": "__catalog__"} if catalog else connection.model_dump()
    )
    return await prepare_runtime(
        application,
        SimpleNamespace(config=draft.model_dump(), secret_ciphertext=ciphertext),
        synthetic=True,
        model_context=model_context,
        response_bytes=1048576 if catalog else 65536,
    )

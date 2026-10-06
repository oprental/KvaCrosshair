"""Release maintenance only: export MIT Anime2Sketch to ONNX, never bundle torch.

Architecture: https://github.com/Mukosame/Anime2Sketch/blob/master/model.py
Place the upstream model.py (as anime2sketch.py) and official improved.bin in
build/lineart-model. Run with build/model-export-env/Scripts/python.exe.
"""
import functools
import hashlib
from pathlib import Path
import sys
import torch
import onnx

folder = Path('build/lineart-model').resolve()
sys.path.insert(0, str(folder))
from anime2sketch import UnetGenerator, Upsample

torch.set_num_threads(2)
net = UnetGenerator(3, 1, 8, 64, norm_layer=functools.partial(
    torch.nn.InstanceNorm2d, affine=False, track_running_stats=False))
weights = folder / 'improved.bin'
if weights.exists():
    with weights.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == 'd2913793286bdeb32f340e2f64e54154ab291daf43f5a352f73543cd3a5a3248'
    base = net.model.model[1]
    for _ in range(6):
        layer = base.model[5]
        base.model[5] = Upsample(layer.in_channels, layer.out_channels)
        base = base.model[3]
else:
    weights = folder / 'netG.pth'
    with weights.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == 'ccabdcc3f5cf3c07cf65d58776acb21df7dfda825cdc70c9766a93fd62bfc488'
state = torch.load(weights, map_location='cpu', weights_only=True)
net.load_state_dict({key.removeprefix('module.'): value for key, value in state.items()})
net.eval()
target = folder / 'anime-lineart.onnx'
torch.onnx.export(net, torch.zeros(1, 3, 512, 512), str(target),
                  input_names=['image'], output_names=['sketch'],
                  dynamic_axes={'image': {2: 'height', 3: 'width'},
                                'sketch': {2: 'height', 3: 'width'}},
                  opset_version=17, dynamo=False, external_data=False)
onnx.checker.check_model(str(target))
with target.open('rb') as stream:
    print('ONNX SHA256:', hashlib.file_digest(stream, 'sha256').hexdigest())
print('ONNX bytes:', target.stat().st_size, 'weights:', weights.name)

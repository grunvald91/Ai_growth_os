import io

import pytest
from PIL import Image,ImageDraw

from app.content.whiteboard.contracts import WhiteboardRegion,WhiteboardRegionPlan,WhiteboardSceneAssets
from app.content.whiteboard.layout import complete_diagram_regions
from app.content.whiteboard.render import MarkerSweepRenderer,_validate_regions
from test_whiteboard import storyboard


def diagram(*,connected=False):
    image=Image.new('RGB',(180,320),'white');d=ImageDraw.Draw(image)
    d.rectangle((60,25,125,38),fill='black')
    d.rectangle((12,115,70,215),outline='black',width=3)
    d.rectangle((105,115,165,215),outline='black',width=3)
    if connected:d.line((60,160,120,160),fill='black',width=5)
    out=io.BytesIO();image.save(out,format='PNG');return image,out.getvalue()


@pytest.mark.parametrize('overlap',[False,True])
def test_offline_plan_repairs_whole_diagrams_without_cutting_or_early_second_reveal(overlap):
    image,png=diagram();scene=storyboard().scenes[0]
    raw=WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0,box=(0,300,800 if overlap else 400,450)),
        WhiteboardRegion(beat_index=1,box=(600,300,950,450))])
    plan,audit=complete_diagram_regions(scene,png,raw)
    assert audit['provider_calls']==0 and audit['final_ink_coverage']==1 and audit['image_unchanged']
    assets=WhiteboardSceneAssets(scene,png,(b'one',b'two'),plan);_validate_regions(assets)
    first=MarkerSweepRenderer(width=180,height=320).frame(image,assets,(2.,2.),1.99)
    assert first.getpixel((12,150))==(0,0,0) and first.getpixel((105,150))==(255,255,255)
    final=MarkerSweepRenderer(width=180,height=320).frame(image,assets,(2.,2.),3.99)
    assert final.tobytes()==image.tobytes()


def test_valid_complete_plan_is_preserved():
    _,png=diagram();scene=storyboard().scenes[0]
    plan=WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0,box=(0,0,1000,200)),
        WhiteboardRegion(beat_index=0,box=(0,200,500,1000)),WhiteboardRegion(beat_index=1,box=(500,200,1000,1000))])
    assert complete_diagram_regions(scene,png,plan)==(plan,None)


def test_connected_artwork_falls_back_to_disjoint_split_flagged_for_review():
    # Previously a ValueError that no resume could get past: the cached regions failed identically every time.
    _,png=diagram(connected=True);scene=storyboard().scenes[0]
    plan=WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0,box=(0,0,800,1000)),
        WhiteboardRegion(beat_index=1,box=(600,0,1000,1000))])
    result,audit=complete_diagram_regions(scene,png,plan)
    assert audit['operation']=='offline_fallback_region_split' and audit['human_review_required']
    assert audit['reason']=='overlap' and audit['provider_calls']==0 and audit['method']=='blank_row'
    _validate_regions(WhiteboardSceneAssets(scene,png,(b'one',b'two'),result))


def test_beat_without_region_gets_fallback_instead_of_failing():
    _,png=diagram();scene=storyboard().scenes[0]
    plan=WhiteboardRegionPlan(regions=[WhiteboardRegion(beat_index=0,box=(0,0,1000,1000))])
    result,audit=complete_diagram_regions(scene,png,plan)
    assert audit['reason']=='beat_without_region' and {r.beat_index for r in result.regions}=={0,1}
    _validate_regions(WhiteboardSceneAssets(scene,png,(b'one',b'two'),result))

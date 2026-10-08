"""Source-checked general examples; never represented as clinically reviewed rehab."""
SOURCE='https://www.nhs.uk/live-well/exercise/strength-exercises/'
EXERCISES=[
 {'id':'sit_to_stand','name':'椅子坐站','equipment':'bodyweight','region':'lower','cues':'使用稳固无轮椅子，双脚平放，缓慢站起与坐下；只在动作已熟悉且不会引发不适时选用，出现疼痛停止。'},
 {'id':'wall_press_up','name':'墙壁俯卧撑','equipment':'bodyweight','region':'upper','cues':'双手扶墙，身体保持直线，缓慢靠近并推回；动作不适时停止。'},
 {'id':'calf_raise','name':'扶稳提踵','equipment':'bodyweight','region':'lower','cues':'扶稳支撑物，缓慢抬起并放下脚跟；动作不适时停止。'},
 {'id':'side_leg_lift','name':'扶稳侧抬腿','equipment':'bodyweight','region':'lower','cues':'扶稳支撑物，保持躯干稳定，缓慢侧抬腿；动作不适时停止。'},
 {'id':'band_biceps_curl','name':'弹力带弯举','equipment':'resistance_band','region':'upper','source':'https://www.mayoclinic.org/healthy-lifestyle/fitness/multimedia/biceps-curl/vid-20084666','cues':'按来源示范固定弹力带，掌心向上，肘部贴近身体，缓慢屈肘并还原；保持腕部稳定，不甩动手臂。固定方式、器械完整性或动作耐受不确定时暂不练习。'},
 {'id':'band_seated_row','name':'弹力带坐姿划船','equipment':'resistance_band','region':'upper','source':'https://www.mayoclinic.org/healthy-lifestyle/fitness/multimedia/seated-row/vid-20084669','cues':'按来源示范在脚部固定弹力带，膝略弯、背部保持自然位置，肘部向后拉再缓慢还原；不弓背或过度后仰。坐姿或固定方式不适时停止，不把此动作当膝痛替代处方。'},
]
for exercise in EXERCISES:
    exercise.update(source=exercise.get('source',SOURCE),review_status='source_checked_not_clinically_reviewed',version=1)
BY_ID={e['id']:e for e in EXERCISES}

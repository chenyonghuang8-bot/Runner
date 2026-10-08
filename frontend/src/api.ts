let csrf = ''
const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'
export function setCsrf(value: string){csrf=value}
export class ApiError extends Error { constructor(message:string, public status:number){super(message)} }
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
 const headers=new Headers(options.headers)
 if(options.body && !(options.body instanceof FormData))headers.set('Content-Type','application/json')
 if(options.method && !['GET','HEAD'].includes(options.method))headers.set('X-CSRF-Token',csrf)
 const response=await fetch(base+path,{...options,headers,credentials:'same-origin'})
 const data=await response.json().catch(()=>({message:'服务器返回了无法读取的结果'}))
 if(!response.ok){const detail=data.field_errors?.map((e:{field:string,message:string})=>`${e.field}: ${e.message}`).join('；');throw new ApiError(detail||data.message||'请求失败',response.status)}
 return data as T
}
export const json=(method:string,value:unknown):RequestInit=>({method,body:JSON.stringify(value)})
export type User={id:string;username:string;csrf_token:string}
export type Profile={display_name:string;age:number|null;sex:'male'|'female'|'unspecified';running_months:number|null;runs_per_week:number;city:string;preferred_time:string;timezone:string;goal_date:string|null;goal_duration_seconds:number|null;health_notes:string;doctor_restrictions:string;running_permission:'unknown'|'user_reports_doctor_allows';medical_review_date:string|null;strength_experience:'unknown'|'none'|'some'|'experienced';strength_equipment:string[];strength_max_minutes:number|null;band_resistance:string;familiar_exercises:string[];same_day_strength:boolean}
export type Workout={session_context?:'unknown'|'easy'|'long'|'steady'|'race';id:string;date:string;started_time:string|null;sport:'road_run'|'trail_run'|'treadmill';title:string;distance_m:number;duration_seconds:number;avg_heart_rate:number|null;max_heart_rate:number|null;ascent_m:number|null;effort:number|null;pain_notes:string;notes:string;revision:number;pace_seconds_per_km:number;confirmed_metrics?:Record<string,{value:number;unit:string;origin:string;review_id:string;review_source:string}>}
export type Checkin={id:string;date:string;observed_at:string|null;observation_timezone:string|null;related_training_type:'run'|'strength'|null;related_training_id:string|null;feedback_phase:string;fatigue:number;sleep_hours:number|null;pain_level:number|null;pain_location:string;pain_timing:string;notes:string;training_readiness:string;soreness_level:number|null;soreness_location:string;soreness_timing:string;soreness_trend:string;soreness_tolerability:string;function_affected:boolean|null;illness_notes:string}
export type Day={date:string;available:boolean;duration_minutes:number}
export type MetricReview={id:string;field:string;status:'confirmed'|'excluded';value:number|null;unit:string;raw:string;note:string;draft_revision:number;created_at:string;current:boolean}
export type Draft={id:string;original_name:string;width:number;height:number;status:string;fields:Partial<Workout>;revision:number;workout_id:string|null;image_url:string;duplicate?:boolean;recognition_performed:boolean;recognition:Recognition|null;metric_reviews:MetricReview[];possible_duplicate_ids:string[]}
export type Reading={field:string;value:number|string|null;raw:string;unit:string;raw_unit?:string;source_tile_id:number;source_rect:number[];origin:string;issue:string;review_status:string}
export type ReviewGroup={field:string;status:'needs_review'|'conflict'|'invalid'|'unknown';reasons:string[];reading_indices:number[];expected_unit:string}
export type Recognition={cache_key:string;region:{x:number;y:number;width:number;height:number};tiles:{rect:number[];state:string;attempts:number;error:string;retryable:boolean}[];done:number;total:number;complete:boolean;readings:Reading[];review_groups:ReviewGroup[];suggested_fields:Partial<Workout>;issues:string[];lease_until:number}
export type AIUsage={provider:string;model:string;month:string;budget_cny:number;committed_cny:number;calls:number;price_basis:string}
export type Exercise={id:string;name:string;equipment:string;region:string;cues:string;source:string;review_status:string}
export type ExerciseSet={exercise_id:string;repetitions:number|null;seconds:number|null;weight_kg:number|null;resistance:string;effort:number|null;notes:string}
export type StrengthWorkout={id:string;date:string;title:string;duration_seconds:number;status:'completed'|'partial'|'skipped';exercises:ExerciseSet[];effort:number|null;pain_notes:string;notes:string}
export type PlanItem={id:string;date:string;type:'run'|'strength'|'recovery';title:string;duration_minutes:number;purpose:string;intensity:string;stop_condition:string;run_kind?:string;blocks?:{label:string;minutes:number}[];exercises:{exercise_id:string;sets:number;repetitions:number;rest_seconds:number;resistance:string;weight_kg:number|null}[]}
export type WeatherSnapshot={revision:number;city:string;source:string;fetched_at:string;expires_at:string;timezone:string;training_time:string;avoid_rain:boolean;days:{date:string;hour:WeatherHour|null;window_hours:WeatherHour[];window_complete:boolean;blocked_reason:string}[];limitations:string[]}
export type PlanPayload={weather_snapshot?:WeatherSnapshot;scope?:'cycle';end_date?:string;cycle_weeks?:CycleWeek[];ability?:AbilityData;sources?:{title:string;url:string;scope:string}[];summary:string;assessment:string;questions:string[];uncertainties:string[];evidence_ids:string[];sessions:PlanItem[];is_mock:boolean;provider:string;professional_review:string;start_date:string}
export type Proposal=PlanPayload&{id:string;state:string;base_version:number;facts_revision:number;expires_at:string;comparison?:PlanComparisonData;changes:{date:string;before:PlanItem[];after:PlanItem[];reason:string}[]}
export type Plan=PlanPayload&{id:string;version:number;parent_version:number;facts_revision:number;created_at:string}
export type PlanState={plan:Plan|null;facts_revision:number;plan_version:number;needs_review:boolean;attention:string[];proposals:Proposal[]}

export type RunEvidenceData={id:string;date:string;sport:string;title:string;distance_m:number;duration_seconds:number;effort:number|null;pain_notes:string;calculated_pace_seconds_per_km:number;device_average_pace_seconds_per_km:number|null;elapsed_minus_duration_seconds:number|null;confirmed_metric_count:number;ignored_metric_fields:string[];device_metrics:NonNullable<Workout['confirmed_metrics']>;splits:{expected_full_kilometers:number;confirmed_count:number;complete:boolean;missing_kilometers:number[];supported_kilometers_limit:number;fastest_confirmed_seconds:number|null;mean_full_kilometer_seconds:number|null;rows:{kilometer:number;seconds:number;review_id:string}[]};notices:string[];limitations:string[]}

export type RaceCycleData={as_of:string;goal_date:string|null;goal_duration_seconds:number|null;days_remaining:number|null;status:'missing_date'|'past_goal'|'review_framework';blocked_reasons:string[];horizon_days:number;truncated:boolean;limitations:string[];weeks:{index:number;start:string;end:string;phase:string;title:string;state:string;race_day:string|null;focus:{run:string;strength:string;recovery:string}}[]}

export type PlanScheduleSummary={run:{sessions:number;minutes:number};strength:{sessions:number;minutes:number;sets:number};recovery:{sessions:number;minutes:number};total_minutes:number}
export type PlanComparisonData={version:string;has_previous_plan:boolean;same_window:boolean;before_window:{start:string|null;end:string|null};after_window:{start:string;end:string};before:PlanScheduleSummary;after:PlanScheduleSummary;days:{date:string;availability:{available:boolean;duration_minutes:number}|null;before:PlanScheduleSummary;after:PlanScheduleSummary}[];limitations:string[]}

export type AbilityData={rule_version:string;status:'insufficient'|'limited'|'established';road_records:number;occupied_weeks:number;baseline_week_minutes:number;longest_easy_minutes:number;observed_easy_pace:{median_seconds_per_km:number|null;sample_count:number;is_training_prescription:boolean};trend:string;blocked_reasons:string[];reduce:boolean;envelope:{week_run_minutes_cap:number;single_run_minutes_cap:number;strength_sessions_cap:number;strength_sets_cap:number;repetitions_cap:number};goal:{note:string};strength:{completed:number;partial:number;skipped:number};sources:{title:string;url:string;scope:string}[];limitations:string[]}
export type CycleWeek={index:number;start:string;end:string;phase:string;run_minutes_cap:number;single_run_minutes_cap:number;strength_count_cap:number;sets_cap:number}

export type WeatherLocation={location_source?:string;source?:string;ascii_name?:string;id:string;name:string;admin1:string;country:string;latitude:number;longitude:number;timezone:string}
export type WeatherHour={time:string;temperature_2m:number|null;apparent_temperature:number|null;relative_humidity_2m:number|null;precipitation_probability:number|null;precipitation:number|null;wind_speed_10m:number|null;weather_code:number|null}
export type WeatherData={provider:'disabled'|'open_meteo';revision:number;location:WeatherLocation|null;candidates:WeatherLocation[];forecast:{hours:WeatherHour[];fetched_at:string;expires_at:string;source:string}|null;status:string;selected_hour:WeatherHour|null;target_date:string;target_time:string;display_timezone:string;advice:string[];source:string}

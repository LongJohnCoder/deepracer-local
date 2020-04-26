from manager import app, db, current_job
from flask import render_template, request, redirect, jsonify
from random import random
from manager.forms import NewJobForm
from manager.models import TrainingJob
from manager.utils import start_training_job, stop_all_containers
from os import getcwd, listdir
import json
from os.path import isfile, isdir, join
import redis


def str2bool(v):
    return v.lower() in ("yes", "true", "t", "1", "y")


@app.route('/')
@app.route('/index')
def index():
    tracks = [
        "AWS_track",
        "Bowtie_track",
        "Canada_Training",
        "China_track",
        "LGSWide",
        "Mexico_track",
        "New_York_Track",
        "Oval_track",
        "reInvent2019_track",
        "reInvent2019_wide_mirrored",
        "reInvent2019_wide",
        "reinvent_base_jeremiah",
        "reinvent_base",
        "reinvent_carpet",
        "reinvent_concrete",
        "reinvent_wood",
        "Straight_track",
        "Tokyo_Training_track",
        "Vegas_track",
        "Virtual_May19_Train_track",
        "AmericasGeneratedInclStart"
    ]

    cwd = getcwd()
    bucketpath = "{}/data/minio/bucket".format(cwd)
    customfiles_dir = "{}/custom_files".format(bucketpath)
    modeldirs = [('None', '----')]
    # modeldirs = []
    metadata_files = []
    reward_func_files = []
    exclude_dirs = ["DeepRacer-Metrics", "custom_files"]

    for f in listdir(bucketpath):
        if isdir(join(bucketpath, f)):
            if f not in exclude_dirs:
                modeldirs.append((f, f))

    for cf in listdir(customfiles_dir):
        if isfile(join(customfiles_dir, cf)):
            if ".json" in cf:
                metadata_files.append((cf, cf))
            if ".py" in cf:
                reward_func_files.append((cf, cf))

    track_choices = []
    for track in tracks:
        track_choices.append((track, track))

    form = NewJobForm()
    form.track.choices = track_choices
    form.pretrained_model.choices = modeldirs
    form.model_metadata_filename.choices = metadata_files
    form.reward_function_filename.choices = reward_func_files
    return render_template('index.html', form=form)


@app.route('/jobs', methods=["GET", "POST"])
def jobs():
    if request.method == "POST":
        form = NewJobForm()
        data = request.form.to_dict(flat=True)
        del data['csrf_token']
        app.logger.info("Saving job: %s" % data)
        if not data['name']:
            return ("Must provide job name", 400)
        if not data['track']:
            return ("Must provide track", 400)
        if not data['model_metadata_filename']:
            return ("Must provide model metadata filename", 400)
        if not data['reward_function_filename']:
            return ("Must provide reward function filename", 400)

        if data['id']:
            app.logger.info("Editing job {}".format(data['id']))
            this_job = TrainingJob.query.get(data['id'])
        else:
            this_job = TrainingJob()
            db.session.add(this_job)

        this_job.name = data['name']
        this_job.race_type = data['race_type']
        this_job.track = data['track']
        this_job.reward_function_filename = data['reward_function_filename']
        this_job.model_metadata_filename = data['model_metadata_filename']
        this_job.episodes = int(data['episodes'])
        this_job.episodes_between_training = int(data['episodes_between_training'])
        this_job.batch_size = int(data['batch_size'])
        this_job.epochs = int(data['epochs'])
        this_job.learning_rate = float(data['learning_rate'])
        this_job.entropy = float(data['entropy'])
        this_job.discount_factor = float(data['discount_factor'])
        this_job.loss_type = data['loss_type']
        this_job.number_of_obstacles = data['number_of_obstacles']
        this_job.randomize_obstacle_locations = str2bool(data['randomize_obstacle_locations'])
        app.logger.debug("change_start: {}".format(data['change_start_position']))
        app.logger.debug("str2bool: {}".format(str2bool(data['change_start_position'])))
        this_job.change_start_position = str2bool(data['change_start_position'])
        this_job.alternate_direction = str2bool(data['alternate_driving_direction'])
        this_job.pretrained_model = data['pretrained_model']

        db.session.commit()

        if this_job:
            jobs = TrainingJob.query.all()
            return ("OK", 200)
        else:
            return ("Failed to save new job", 500)

    else:
        all_jobs = TrainingJob.query.all()
        jobs_array = []
        for this_job in all_jobs:
            jobs_array.append(this_job.as_dict())
        # app.logger.info(json.dumps(jobs_array))
        return jsonify(jobs_array)


@app.route('/job/<job_id>', methods=["GET", "POST", "DELETE"])
def job(job_id):
    app.logger.info("in job({})".format(job_id))
    # job_id = request.args.get('job_id', None)

    if not job_id:
        return "Job not found", 404

    if request.method == "DELETE":
        app.logger.info("Got delete for job {}".format(job_id))
        job_to_delete = TrainingJob.query.filter_by(id=job_id).first()
        if job_to_delete:
            app.logger.info("Found job: {}".format(job_to_delete))
            if current_job.training_job == job_to_delete:
                current_job.training_job = None
            db.session.delete(job_to_delete)
            db.session.commit()
        return "OK", 200

    if request.method == "GET":
        this_job = TrainingJob.query.get(job_id)
        return jsonify(this_job.as_dict())


@app.route('/current_job', methods=["GET", "POST"])
def current_training_job():
    if request.method == "POST":
        request_data = request.json
        if request_data['action'] == "start_training":
            app.logger.info("Starting training session")
            current_job.desired_state = "running"
            current_job.update_status()
            if not current_job.status['sagemaker_status'] and not current_job.status['robomaker_status']:
                app.logger.info("ok to start")
                first_job = TrainingJob.query.filter_by(status='queued').first()
                if first_job:
                    start_training_job(first_job)
                else:
                    app.logger.warning("Start button clicked but no jobs in queued state")
                    return "No queued jobs", 400
            else:
                app.logger.info("Not ok to start")
                return "not ready", 412

        if request_data['action'] == "stop_training":
            app.logger.info("Stop training requested")
            current_job.desired_state = "stopped"
            try:
                training_job = TrainingJob.query.get(current_job.training_job_id)
                db.session.refresh(training_job)
                training_job.status = "stopped"
                db.session.commit()
            except Exception as e:
                app.logger.error("Error while stopping: {}".format(e))

            stop_all_containers()

        return "OK"
    else:
        current_job.update_status()
        return jsonify(current_job.status)


# @app.route('/metrics', methods=["GET"])
# def get_metrics():
#     from_episode = request.args.get('from_episode', -1)
#     metrics_to_return = []
#     for metric in current_job.metrics:
#         if metric['phase'] == "training" and metric['episode'] > int(from_episode):
#             metrics_to_return.append(metric)
#             print(metric)
#
#         if metric['phase'] == "evaluation" and metric['episode'] >= int(from_episode):
#             metrics_to_return.append(metric)
#             print(metric)
#
#     return jsonify(metrics_to_return)


@app.route('/metrics/<job_id>', methods=["GET"])
def get_metrics(job_id):
    from_episode = request.args.get('from_episode', -1)
    metrics_to_return = []
    r = redis.Redis()

    key = "metrics-{}".format(job_id)
    for i in range(0, r.llen(key)):
        metric = json.loads(r.lindex(key, i).decode("utf-8"))

        if metric['phase'] == "training" and metric['episode'] > int(from_episode):
            metrics_to_return.append(metric)
            print(metric)

        if metric['phase'] == "evaluation" and metric['episode'] >= int(from_episode):
            metrics_to_return.append(metric)
            print(metric)

    return jsonify(metrics_to_return)


@app.route('/pretrained_dirs', methods=["GET"])
def pretrained_dirs():
    cwd = getcwd()
    bucketpath = "{}/data/minio/bucket".format(cwd)
    modeldirs = [('None', '----')]
    exclude_dirs = ["DeepRacer-Metrics", "custom_files"]

    for f in listdir(bucketpath):
        if isdir(join(bucketpath, f)):
            if f not in exclude_dirs:
                modeldirs.append({"value": f, "text": f})

    queued_jobs = TrainingJob.query.filter_by(status="queued").all()
    for qj in queued_jobs:
        modeldirs.append({"value": qj.name, "text": qj.name})

    return jsonify(modeldirs)
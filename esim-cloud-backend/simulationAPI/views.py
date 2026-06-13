from simulationAPI.serializers import TaskSerializer, \
    simulationSerializer, simulationSaveSerializer
from simulationAPI.tasks import process_task
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from rest_framework.exceptions import ValidationError
from celery.result import AsyncResult
from saveAPI.models import StateSave
import uuid
from .models import runtimeStat, Limit, simulation
from ltiAPI.models import ltiSession
import celery.signals
from celery import current_task
import time
import math
import os
import logging
import requests

logger = logging.getLogger(__name__)


def saveNetlistDB(task_id, filepath, request):
    current_dir = settings.FILE_STORAGE_ROOT
    filepath = filepath.split('/')[-1]
    os.chdir(current_dir)
    f = open(filepath, "r")
    temp = f.read()
    if request.user.is_authenticated:
        owner = request.user.id
    else:
        owner = None
    if request.data.get('simulationType', None):
        simulation_type = request.data['simulationType']
    else:
        simulation_type = "NgSpiceSimulator"
    if request.data.get('save_id', None):
        if 'gallery' in request.data.get('save_id'):
            save_id = None
        else:
            save_id = StateSave.objects.get(
                save_id=request.data['save_id'],
                version=request.data['version'],
                branch=request.data['branch']).id
    else:
        save_id = None
    lti_session = None
    if request.data.get('lti_id', None):
        lti_session = ltiSession.objects.get(id=request.data['lti_id'])
    serialized = simulationSaveSerializer(
        data={"task": task_id, "netlist": temp, "owner": owner,
              "simulation_type": simulation_type, "schematic": save_id})
    if serialized.is_valid(raise_exception=True):
        serialized.save()
        if lti_session:
            lti_session.simulations.add(
                simulation.objects.get(id=serialized.data['id']))
        return
    else:
        return Response(serialized.errors)


class NetlistUploader(APIView):
    permission_classes = (AllowAny,)
    parser_classes = (MultiPartParser, FormParser,)

    def post(self, request, *args, **kwargs):
        logger.info('Got POST for netlist upload: ')
        logger.info(request.data)

        # ✅ NEW: User ID nikalo
        if request.user.is_authenticated:
            user_id = str(request.user.username)  # username use karo — unique hota hai
        else:
            user_id = request.META.get('REMOTE_ADDR', 'anonymous').replace('.', '')

        # ✅ NEW: Session Manager ko call karo — K8s pod create hoga
        try:
            session_manager_url = getattr(
                settings, 'SESSION_MANAGER_URL', 'http://localhost:8002'
            )
            session_response = requests.post(
                f"{session_manager_url}/session/start",
                json={"user_id": user_id, "namespace": "default"},
                timeout=10
            )
            if session_response.status_code == 200:
                pod_info = session_response.json()
                logger.info(f"✅ Pod created/found: {pod_info['pod_name']} for user: {user_id}")
            else:
                logger.error(f"❌ Session manager error: {session_response.text}")
        except Exception as e:
            logger.error(f"❌ Session manager unreachable: {e}")
            # Session manager fail hone par bhi simulation chalne do

        # Baaki code same
        serializer = TaskSerializer(data=request.data, context={'view': self})

        limits = Limit.objects.all()
        TIME_LIMIT = 0
        if limits.exists():
            TIME_LIMIT = Limit.objects.all()[0].timeLimit

        if serializer.is_valid():
            serializer.save()
            saveNetlistDB(
                serializer.data['task_id'], serializer.data['file'][0]['file'],
                request)
            task_id = serializer.data['task_id']
            if TIME_LIMIT == 0:                celery_task = process_task.apply_async(
                    kwargs={'task_id': str(task_id)}, task_id=str(task_id)
                )
            else:
                celery_task = process_task.apply_async(
                    kwargs={'task_id': str(task_id)}, task_id=str(task_id),
                    soft_time_limit=TIME_LIMIT)

            response_data = {
                'state': celery_task.state,
                'details': serializer.data,
            }
            return Response(response_data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CeleryResultView(APIView):
    permission_classes = (AllowAny,)
    methods = ['GET']

    def get(self, request, task_id):
        if isinstance(task_id, uuid.UUID):
            celery_result = AsyncResult(str(task_id))
            response_data = {
                'state': celery_result.state,
                'details': celery_result.info
            }
            try:
                Output = simulation.objects.get(task__task_id=task_id)
                Output.result = celery_result.info
                Output.save()
            except simulation.DoesNotExist:
                pass
            return Response(response_data)
        else:
            raise ValidationError('Invalid uuid format')


class SimulationResults(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, save_id, sim, version, branch):
        sims = simulation.objects.filter(
            owner=self.request.user, schematic__save_id=save_id,
            schematic__version=version, schematic__branch=branch
        )
        serialized = simulationSerializer(sims, many=True)
        return Response(serialized.data, status=status.HTTP_200_OK)


class SimulationResultsForLTI(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, save_id, sim, version, branch):
        sims = simulation.objects.filter(
            owner=self.request.user, schematic__save_id=save_id
        )
        serialized = simulationSerializer(sims, many=True)
        return Response(serialized.data, status=status.HTTP_200_OK)


class SimulationResultsFromSimulator(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, sim):
        sims = simulation.objects.filter(
            owner=self.request.user, simulation_type=sim)
        serialized = simulationSerializer(sims, many=True)
        return Response(serialized.data, status=status.HTTP_200_OK)


class GetLTISimResults(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, lti_id):
        try:
            session = ltiSession.objects.get(id=lti_id)
            serialized = simulationSerializer(
                session.simulations.all(), many=True)
            return Response(serialized.data, status=status.HTTP_200_OK)
        except ltiSession.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


@celery.signals.task_prerun.connect
def statsd_task_prerun(task_id, **kwargs):
    current_task.start_time = time.time()


@celery.signals.task_postrun.connect
def statsd_task_postrun(task_id, **kwargs):
    runtime = time.time() - current_task.start_time
    runtime = math.ceil(runtime)
    statObj, created = runtimeStat.objects.get_or_create(exec_time=runtime)
    statObj.qty += 1
    statObj.save()
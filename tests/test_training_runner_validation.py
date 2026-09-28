import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('training_runner',Path(__file__).resolve().parents[1]/'scripts/train_gpt_oss_sft.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


@pytest.fixture
def data(tmp_path):
    p=tmp_path/'dataset.jsonl'
    p.write_text(json.dumps({'messages':[{'role':'user','content':'Explain my reasoning.'},
                                        {'role':'assistant','content':'Which condition supports that step?'}]})+'\n')
    manifest={'training_status':'ELIGIBLE','snapshot_hash':'a'*64,'dataset_file_sha256':runner.sha256_file(p),
              'record_count':1,'source_classes':['OWNER_AUTHORED'],'allowed_use_scopes':['TUTOR_POLICY'],
              'rights_state':'CONFIRMED','consent_state':'NOT_REQUIRED','provenance_complete':True}
    return p,manifest


def test_reviewed_sft_passes(data):
    p,m=data;runner.validate_manifest(dataset_path=p,manifest=m)


@pytest.mark.parametrize('field,value',[
    ('source_classes',[]),('source_classes',['THIRD_PARTY_API_OUTPUT']),
    ('allowed_use_scopes',['ITEM_GENERATOR']),('rights_state','UNKNOWN'),
    ('consent_state','REVOKED'),('record_count',2),('snapshot_hash','z'*64),
    ('source_classes',['FIRST_PARTY_TUTORING']),
])
def test_unreviewed_or_mislabelled_data_stops_before_gpu(data,field,value):
    p,m=data;m[field]=value
    with pytest.raises(ValueError): runner.validate_manifest(dataset_path=p,manifest=m)


def test_metadata_only_snapshot_cannot_be_misreported_as_training_data(data):
    p,m=data;p.write_text(json.dumps({'evidence_refs':['e1']})+'\n');m['dataset_file_sha256']=runner.sha256_file(p)
    with pytest.raises(ValueError,match='conversation'):runner.validate_manifest(dataset_path=p,manifest=m)

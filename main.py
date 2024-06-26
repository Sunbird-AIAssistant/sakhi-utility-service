import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from env_manager import storage_class
from few_shot_util import *
from io_processing import *
from logger import logger
from telemetry_middleware import TelemetryMiddleware
from utils import is_base64, is_url

app = FastAPI()


class HealthCheck(BaseModel):
    """Response model to validate and return when performing a health check."""
    status: str = "OK"


class ContextRequest(BaseModel):
    text: str = None
    audio: str = None
    language: str = None


class QueryInputModel(BaseModel):
    language: str = None
    text: str = None
    audio: str = None


class QueryOutputModel(BaseModel):
    format: str = None
    audio: str = None
    language: str = None


class TranslationRequest(BaseModel):
    input: QueryInputModel
    output: QueryOutputModel


class OutputResponse(BaseModel):
    text: str = None
    audio: str = None


class TranslationResponse(BaseModel):
    translation: OutputResponse = None


language_code_list = get_from_env_or_config('lang_code', 'supported_lang_codes', None).split(",")

# Telemetry API logs middleware
app.add_middleware(TelemetryMiddleware)


@app.get(
    "/health",
    tags=["Health Check"],
    summary="Perform a Health Check",
    response_description="Return HTTP Status Code 200 (OK)",
    status_code=status.HTTP_200_OK,
    response_model=HealthCheck,
    include_in_schema=True
)
def get_health() -> HealthCheck:
    """
    ## Perform a Health Check
    Endpoint to perform a healthcheck on. This endpoint can primarily be used Docker
    to ensure a robust container orchestration and management is in place. Other
    services which rely on proper functioning of the API service will not deploy if this
    endpoint returns any other HTTP status code except 200 (OK).
    Returns:
        HealthCheck: Returns a JSON response with the health status
    """
    return HealthCheck(status="OK")


@app.post("/v1/context", tags=["API for fetching query context information"])
async def query_context_extraction(request: ContextRequest):
    load_dotenv()

    text = None
    audio = None
    source_language = None
    updated_answer = None

    min_words_length = get_from_env_or_config('min_words', 'length', None)
    logger.info({"text": request.text, "audio": request.audio, "source_language": request.language})
    if request.text is not None:
        text = request.text.strip()
    if request.audio is not None:
        audio = request.audio.strip()
    if request.language is not None:
        source_language = request.language.strip().lower()

    logger.info({"text": text, "audio": audio, "source_language": source_language})
    if text is None and audio is None:
        raise HTTPException(status_code=422, detail="Either 'text' or 'audio' should be present!")
    elif (text is None or text == "") and (audio is None or audio == ""):
        raise HTTPException(status_code=422, detail="Either 'text' or 'audio' should be present!")
    elif text is not None and audio is not None and text != "" and audio != "":
        raise HTTPException(status_code=422, detail="Both 'text' and 'audio' cannot be taken as input! Either 'text' "
                                                    "or 'audio' is allowed.")
    elif source_language is None or source_language == "":
        raise HTTPException(status_code=400, detail="Invalid Request! Please provide Source Language,!")
    else:
        try:
            if source_language is None or source_language == "" or source_language not in language_code_list:
                raise HTTPException(status_code=400, detail="Unsupported language!")
        except Exception:
            raise HTTPException(status_code=400, detail="Unsupported language!")

        if text is not None and text != "":
            logger.info({"text": text, "source_language": source_language})
            src_lang_text = text
            eng_text, error_message = translate_text_to_english(text, source_language)
            if error_message:
                raise HTTPException(status_code=503, detail="Failed to translate!")
        else:
            if not is_url(audio) and not is_base64(audio):
                raise HTTPException(status_code=422, detail="Invalid audio input!")
            logger.info({"source_language:", source_language})
            src_lang_text, eng_text, error_message = transcribe_audio_to_reg_eng_text(audio, source_language)
            if error_message:
                raise HTTPException(status_code=503, detail="Failed to translate!")
            logger.info({"src_lang_text:", src_lang_text, "eng_text:", eng_text})

        logger.info({"query": eng_text})
        try:
            response = json.loads("{" + invokeLLM(eng_text) + "}")
            answer: dict = response["answer"]
            logger.info("answer:: ", answer)
        except Exception as ex:
            answer = None
            logger.info(ex)
            logger.error(f"Exception occurred: {ex}", exc_info=True)

    if len(eng_text.split(" ")) < int(min_words_length):
        updated_answer = {"keywords": answer["keywords"]} if answer is not None and "keywords" in answer else None
    else:
        updated_answer = remove_keys_with_any(answer) if answer is not None else None

    response = {
        "input": {
            "sourceText": src_lang_text,
            "englishText": eng_text
        },
        "context": updated_answer
    }
    logger.info({"response": response})
    return response


@app.post("/v1/translation", tags=["API for translation of text and audio in English and Indic languages"])
async def translator(request: TranslationRequest) -> TranslationResponse:
    load_dotenv()
    text = None
    audio = None
    source_language = None
    target_language = None
    target_format = None
    trans_text = None
    trans_audio = None

    if request.input.text is not None:
        text = request.input.text.strip()
    if request.input.audio is not None:
        audio = request.input.audio.strip()
    if request.input.language is not None:
        source_language = request.input.language.strip().lower()
    if request.output.language is not None:
        target_language = request.output.language.strip().lower()
    if request.output.format is not None:
        target_format = request.output.format.strip().lower()

    logger.info(
        {"text": text, "audio": audio, "source_language": source_language, "target_language": target_language,
         "target_format": target_format})
    if text is None and audio is None:
        raise HTTPException(status_code=422, detail="Either 'text' or 'audio' should be present!")
    elif (text is None or text == "") and (audio is None or audio == ""):
        raise HTTPException(status_code=422, detail="Either 'text' or 'audio' should be present!")
    elif text is not None and audio is not None and text != "" and audio != "":
        raise HTTPException(status_code=422, detail="Both 'text' and 'audio' cannot be taken as input! Either 'text' "
                                                    "or 'audio' is allowed.")
    elif source_language is None or source_language == "":
        raise HTTPException(status_code=400, detail="Invalid Request! Please provide valid source Language!")
    elif target_format != "text" and target_format != "audio":
        raise HTTPException(status_code=400, detail="Please provide valid target format: text or audio!")
    elif (source_language == target_language) and (
            (text is not None and text != "" and target_format == "text") or (
            audio is not None and audio != "" and target_format == "audio")):
        raise HTTPException(status_code=400, detail="Invalid Request! Please check Source Language, Target Language,"
                                                    "Input Format and Output Format combination.")
    else:
        try:
            if source_language is None or source_language == "" or source_language not in language_code_list:
                raise HTTPException(status_code=400, detail="Unsupported source language!")
        except Exception:
            raise HTTPException(status_code=400, detail="Unsupported source language!")

        try:
            if target_language is None or target_language == "" or target_language not in language_code_list:
                raise HTTPException(status_code=400, detail="Unsupported target language!")
        except Exception:
            raise HTTPException(status_code=400, detail="Unsupported target language!")

        if target_format == "text" and text is not None and text != "":
            logger.info("TRANSLATE TEXT TO TEXT OF OTHER LANGUAGE::: ")
            logger.info({"text": text, "source_language": source_language, "target_language": target_language})
            trans_text, error_message = translate_text(text, source_language, target_language)
        elif target_format == "audio" and text is not None and text != "" and source_language == target_language:
            logger.info("TRANSLATE TEXT TO AUDIO OF SAME LANGUAGE::: ")
            logger.info({"text": text, "source_language": source_language})
            trans_audio = convert_to_audio(text, source_language)
        elif target_format == "audio" and text is not None and text != "" and source_language != target_language:
            logger.info("TRANSLATE TEXT TO AUDIO OF OTHER LANGUAGE::: ")
            logger.info({"text": text, "source_language": source_language, "target_language": target_language})
            trans_text, error_message = translate_text(text, source_language, target_language)
            trans_audio = convert_to_audio(trans_text, target_language)
        elif target_format == "text" and audio is not None and audio != "" and source_language == target_language:
            if not is_url(audio) and not is_base64(audio):
                raise HTTPException(status_code=422, detail="Invalid audio input!")
            logger.info("TRANSLATE AUDIO TO TEXT OF SAME LANGUAGE::: ")
            logger.info({"text": text, "source_language": source_language})
            trans_text = translator.speech_to_text(audio, source_language)
        elif target_format == "text" and audio is not None and audio != "" and source_language != target_language:
            if not is_url(audio) and not is_base64(audio):
                raise HTTPException(status_code=422, detail="Invalid audio input!")
            logger.info("TRANSLATE AUDIO TO TEXT OF OTHER LANGUAGE::: ")
            logger.info({"text": text, "source_language": source_language, "target_language": target_language})
            trans_text_same_lang = translator.speech_to_text(audio, source_language)
            trans_text, error_message = translate_text(trans_text_same_lang, source_language, target_language)
        elif target_format == "audio" and audio is not None and audio != "":
            if not is_url(audio) and not is_base64(audio):
                raise HTTPException(status_code=422, detail="Invalid audio input!")
            logger.info("TRANSLATE AUDIO TO AUDIO OF OTHER LANGUAGE::: ")
            src_trans_text = translator.speech_to_text(audio, source_language)
            trans_text, error_message = translate_text(src_trans_text, source_language, target_language)
            trans_audio = convert_to_audio(trans_text, target_language)

    response = TranslationResponse()
    op_resp = OutputResponse()
    op_resp.text = trans_text
    op_resp.audio = trans_audio
    response.translation = op_resp
    logger.info(msg=response)
    return response


def convert_to_audio(text, target_language):
    output_file, error_message = convert_text_to_audio(text, target_language)
    logger.debug("Output File:: ", output_file.name)
    if output_file is not None:
        storage_class.upload_to_storage(output_file.name)
        trans_audio_url, error_message = storage_class.generate_public_url(output_file.name)
        logger.debug("Audio Output URL:: ", trans_audio_url)
        output_file.close()
        os.remove(output_file.name)
        return trans_audio_url
    else:
        raise HTTPException(status_code=503, detail="Failed to generate a response!")


def remove_keys_with_any(dict_obj):
    new_dict = {}
    for key, value in dict_obj.items():
        if isinstance(value, list) and "Any" in value:
            continue
        elif isinstance(value, dict):
            new_dict[key] = remove_keys_with_any(value)
        else:
            new_dict[key] = value
    return new_dict

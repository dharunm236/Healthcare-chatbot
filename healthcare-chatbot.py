import streamlit as st
import transformers
from dateparser import parse
import datetime
from icalendar import Calendar, Event
from huggingface_hub import login
import os
import torch

# Add Hugging Face authentication
def authenticate_huggingface():
    if 'hf_token' not in st.session_state:
        st.session_state.hf_token = None
        
    if st.session_state.hf_token is None:
        with st.sidebar:
            st.write("## Hugging Face Login")
            hf_token = st.text_input("Enter Hugging Face Token:", type="password")
            if st.button("Login"):
                try:
                    if hf_token == "use_default_token":
                        hf_token = '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'
                    login(token=hf_token)
                    st.session_state.hf_token = hf_token
                    st.success("Successfully logged in to Hugging Face!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {str(e)}")
        return False
    return True

def initialize_chatbot():
    """Initialize the chatbot with optimized settings"""
    model_id = "ContactDoctor/Bio-Medical-Llama-3-2-1B-CoT-012025"
    
    pipeline = transformers.pipeline(
        "text-generation",
        model=model_id,
        model_kwargs={"torch_dtype": torch.bfloat16},
        device_map="auto",
    )
    return pipeline

# Initialize session state
if 'chat_sessions' not in st.session_state:
    st.session_state.chat_sessions = [{
        "messages": [],
        "booking": {
            'in_progress': False,
            'stage': None,
            'details': {}
        }
    }]
if 'current_session' not in st.session_state:
    st.session_state.current_session = 0

# Modify health_bot function to use authenticated chatbot
def health_bot(user_input):
    user_input_lower = user_input.lower()
    current_session = st.session_state.chat_sessions[st.session_state.current_session]
    booking = current_session['booking']

    # Check for cancellation at any stage
    if booking['in_progress'] and user_input_lower == 'cancel':
        booking['in_progress'] = False
        booking['stage'] = None
        booking['details'] = {}
        return "Appointment booking cancelled.", None

    if booking['in_progress']:
        current_stage = booking['stage']
        
        if current_stage == 'name':
            booking['details']['doctor'] = user_input
            booking['stage'] = 'date'
            return "Great. On which date would you like to schedule the appointment? (e.g., 'June 25th' or 'next Monday')", None
        
        elif current_stage == 'date':
            parsed_date = None
            if user_input_lower in ['today', 'tomorrow']:
                days_delta = 1 if user_input_lower == 'tomorrow' else 0
                parsed_date = datetime.datetime.now() + datetime.timedelta(days=days_delta)
            else:
                parsed_date = parse(user_input, settings={
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': datetime.datetime.now()
                })
            
            if parsed_date:
                # Ensure the date is not in the past
                if parsed_date.date() < datetime.datetime.now().date():
                    return "Please select a future date. You can't book appointments in the past.", None
                
                booking['details']['date'] = parsed_date
                booking['stage'] = 'time'
                return "What time works for you? (e.g., '10 AM' or '3:30 PM')", None
            else:
                return "I couldn't understand that date. Please use the calendar picker or enter a date like 'tomorrow' or 'next Monday'.", None
        
        elif current_stage == 'time':
            parsed_time = parse(user_input)
            if parsed_time:
                try:
                    date_obj = booking['details']['date']
                    combined_datetime = datetime.datetime.combine(
                        date_obj.date(),
                        parsed_time.time()
                    )
                    booking['details']['datetime'] = combined_datetime
                    booking['stage'] = 'confirm'
                    formatted_date = combined_datetime.strftime('%A, %B %d %Y at %I:%M %p')
                    return f"Confirming: Appointment with {booking['details']['doctor']} on {formatted_date}. Should I book this? (Please respond 'confirm' or 'cancel')", None
                except Exception as e:
                    return "There was an error processing the time. Please try again.", None
            else:
                return "I couldn't understand that time. Please try again.", None
        
        elif current_stage == 'confirm':
            if user_input_lower == 'confirm':
                appointment_details = {
                    'doctor': booking['details']['doctor'],
                    'datetime': booking['details']['datetime'].isoformat(),
                    'summary': f"Appointment with {booking['details']['doctor']}"
                }
                booking['in_progress'] = False
                booking['stage'] = None
                booking['details'] = {}
                return "Appointment booked! Would you like to add this to your calendar?", appointment_details
            else:
                booking['in_progress'] = False
                booking['stage'] = None
                booking['details'] = {}
                return "Appointment booking cancelled.", None
    
    if "appointment" in user_input_lower:
        current_session['booking']['in_progress'] = True
        current_session['booking']['stage'] = 'name'
        return "Sure! Let's schedule that. What's the doctor's name?", None
    
    elif "symptom" in user_input_lower:
        return "I recommend consulting a healthcare professional. Would you like me to help find a nearby clinic?", None
    
    else:
        messages = [
            {"role": "system", "content": "You are an expert trained on healthcare and biomedical domain!"},
            {"role": "user", "content": user_input},
        ]

        prompt = st.session_state.chatbot.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        terminators = [
            st.session_state.chatbot.tokenizer.eos_token_id,
            st.session_state.chatbot.tokenizer.convert_tokens_to_ids("<|eot_id|>")
        ]

        outputs = st.session_state.chatbot(
            prompt,
            max_new_tokens=256,
            eos_token_id=terminators,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
        )
        
        response = outputs[0]["generated_text"][len(prompt):]
        return response.strip(), None

def create_ics_file(appointment_details):
    cal = Calendar()
    event = Event()
    
    event.add('summary', appointment_details['summary'])
    event.add('dtstart', datetime.datetime.fromisoformat(appointment_details['datetime']))
    event.add('dtend', datetime.datetime.fromisoformat(appointment_details['datetime']) + datetime.timedelta(hours=1))
    event.add('location', 'Medical Clinic')
    
    cal.add_component(event)
    return cal.to_ical()

# Modified main function
def main():
    st.title("HealthCare Assistant")
    st.caption("Disclaimer: This medical chatbot provides information for reference purposes only and is not a substitute for professional medical advice.")

    # Check authentication before proceeding
    if not authenticate_huggingface():
        st.warning("Please login with your Hugging Face token to continue")
        return

    # Initialize chatbot only after authentication
    if 'chatbot' not in st.session_state:
        with st.spinner("Initializing chatbot..."):
            st.session_state.chatbot = initialize_chatbot()

    # Sidebar with chat history
    with st.sidebar:
        st.header("Chat History")
        if st.button("Start New Chat"):
            st.session_state.chat_sessions.append({
                "messages": [],
                "booking": {
                    'in_progress': False,
                    'stage': None,
                    'details': {}
                }
            })
            st.session_state.current_session = len(st.session_state.chat_sessions) - 1
        
        for i, session in enumerate(st.session_state.chat_sessions):
            with st.expander(f"Chat Session {i+1}", expanded=(i == st.session_state.current_session)):
                if session["messages"]:
                    preview = session["messages"][-1]["content"][:30] + "..." 
                    st.caption(f"Last message: {preview}")
                    if st.button(f"Open Chat {i+1}", key=f"open_{i}"):
                        st.session_state.current_session = i

    # Main chat interface
    current_session = st.session_state.chat_sessions[st.session_state.current_session]
    current_messages = current_session["messages"]
    
    for idx, msg in enumerate(current_messages):
        st.write(f"**{msg['role'].capitalize()}**: {msg['content']}")
        
        if msg['role'] == 'assistant' and msg.get('appointment'):
            if st.button("Add to Calendar", key=f"calendar_{idx}"):
                ics_data = create_ics_file(msg['appointment'])
                st.download_button(
                    label="Download Calendar Event",
                    data=ics_data,
                    file_name="appointment.ics",
                    mime="text/calendar",
                    key=f"download_{idx}"
                )

    # Chat input form
    with st.form(key="chat_form", clear_on_submit=True):
        current_session = st.session_state.chat_sessions[st.session_state.current_session]
        booking = current_session['booking']
        
        # Show calendar picker when asking for date
        if booking['in_progress'] and booking['stage'] == 'date':
            min_date = datetime.datetime.now().date()
            max_date = min_date + datetime.timedelta(days=90)  # Allow booking up to 90 days in advance
            selected_date = st.date_input(
                "Please select a date:",
                min_value=min_date,
                max_value=max_date,
                key="appointment_date"
            )
            if selected_date:
                user_input = selected_date.strftime('%Y-%m-%d')
            else:
                user_input = st.text_input("Or type a date (e.g., 'tomorrow', 'next Monday'):")
        else:
            user_input = st.text_input("How can I assist you today?")
            
        submit = st.form_submit_button("Submit")

        if submit and user_input:
            current_messages.append({"role": "user", "content": user_input})
            
            with st.spinner("Analyzing..."):
                bot_response, appointment_details = health_bot(user_input)
            
            current_messages.append({
                "role": "assistant",
                "content": bot_response,
                "appointment": appointment_details
            })
            
            st.session_state.chat_sessions[st.session_state.current_session]["messages"] = current_messages
            st.rerun()

if __name__ == "__main__":
    main()